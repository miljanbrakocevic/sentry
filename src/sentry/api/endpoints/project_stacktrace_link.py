from typing import Any, Mapping, Optional

from rest_framework.request import Request
from rest_framework.response import Response
from sentry_sdk import configure_scope

from sentry import analytics
from sentry.api.base import region_silo_endpoint
from sentry.api.bases.project import ProjectEndpoint
from sentry.api.serializers import serialize
from sentry.integrations import IntegrationFeatures
from sentry.models import Integration, RepositoryProjectPathConfig
from sentry.shared_integrations.exceptions import ApiError
from sentry.utils.event_frames import munged_filename_and_frames


def get_link(
    config: RepositoryProjectPathConfig, filepath: str, version: Optional[str] = None
) -> Any:
    result = {}
    oi = config.organization_integration
    integration = oi.integration
    install = integration.get_installation(oi.organization_id)

    formatted_path = filepath.replace(config.stack_root, config.source_root, 1)

    link, attempted_url, error = None, None, None
    try:
        link = install.get_stacktrace_link(
            config.repository, formatted_path, config.default_branch, version
        )
    except ApiError as e:
        if e.code != 403:
            raise
        error = "integration_link_forbidden"

    # If the link was not found, attach the URL that we attempted.
    if not link:
        error = error or "file_not_found"
        attempted_url = install.format_source_url(
            config.repository, formatted_path, config.default_branch
        )
    result = {
        "attemptedUrl": attempted_url,
        "error": error,
        "sourceUrl": link,
    }
    return result


# This is to support mobile languages with non-fully-qualified file pathing.
# We attempt to 'munge' the proper source-relative filepath based on the stackframe data.
def generate_mobile_frame(parameters: Any) -> Any:
    abs_path = parameters.get("absPath")
    module = parameters.get("module")
    package = parameters.get("package")
    frame = {}
    if abs_path:
        frame["abs_path"] = abs_path
    if module:
        frame["module"] = module
    if package:
        frame["package"] = package
    return frame


def try_path_munging(
    config: RepositoryProjectPathConfig,
    filepath: str,
    mobile_frame: Any,
    ctx: Any,
) -> Any:
    result = {
        "error": None,
    }
    mobile_frame["filename"] = filepath
    munged_frames = munged_filename_and_frames(
        ctx["platform"], [mobile_frame], "munged_filename", sdk_name=ctx["sdk_name"]
    )
    if munged_frames:
        munged_frame: Mapping[str, Any] = munged_frames[1][0]
        munged_filename = str(munged_frame.get("munged_filename"))
        if munged_filename:
            if not filepath.startswith(config.stack_root) and not munged_filename.startswith(
                config.stack_root
            ):
                result["error"] = "stack_root_mismatch"

            if not result["error"]:
                result = get_link(config, munged_filename, ctx["commit_id"])
    return result


@region_silo_endpoint
class ProjectStacktraceLinkEndpoint(ProjectEndpoint):
    """
    Returns valid links for source code providers so that
    users can go from the file in the stack trace to the
    provider of their choice.

    `filepath`: The file path from the stack trace
    `commitId` (optional): The commit_id for the last commit of the
                           release associated to the stack trace's event
    `sdkName` (optional): The sdk.name associated with the event
    `absPath` (optional): The abs_path field value of the relevant stack frame
    `module`   (optional): The module field value of the relevant stack frame
    `package`  (optional): The package field value of the relevant stack frame

    """

    def get(self, request: Request, project) -> Response:
        # import pprint

        # pprint.pp(request)
        # should probably feature gate
        filepath = request.GET.get("file")
        if not filepath:
            return Response({"detail": "Filepath is required"}, status=400)

        ctx = {
            "commit_id": request.GET.get("commitId"),
            "platform": request.GET.get("platform"),
            "sdk_name": request.GET.get("sdkName"),
        }
        mobile_frame = generate_mobile_frame(request.GET)

        result = {"config": None, "sourceUrl": None}

        integrations = Integration.objects.filter(organizations=project.organization_id)
        # TODO(meredith): should use get_provider.has_feature() instead once this is
        # no longer feature gated and is added as an IntegrationFeature
        result["integrations"] = [
            serialize(i, request.user)
            for i in integrations
            if i.has_feature(IntegrationFeatures.STACKTRACE_LINK)
        ]

        # xxx(meredith): if there are ever any changes to this query, make
        # sure that we are still ordering by `id` because we want to make sure
        # the ordering is deterministic
        # codepath mappings must have an associated integration for stacktrace linking.
        configs = RepositoryProjectPathConfig.objects.filter(
            project=project, organization_integration__isnull=False
        )
        matched_code_mappings = []
        found = False
        with configure_scope() as scope:
            scope.set_tag("project.slug", project.slug)
            scope.set_tag("organization.slug", project.organization.slug)
            for config in configs:
                # The code mapping does not match and we're not dealing with a mobile platform
                if not filepath.startswith(config.stack_root) and not mobile_frame:
                    continue

                result = get_link(config, filepath, ctx["commit_id"])

                # For mobile we try a second time by munging the file path
                if not result["sourceUrl"] and mobile_frame:
                    result = try_path_munging(filepath, mobile_frame, ctx)

                current_config = {
                    "config": serialize(config, request.user),
                    "result": result,
                }

                # use the provider key to be able to split up stacktrace
                # link metrics by integration type
                provider = result["config"]["provider"]["key"]
                # print("ARMEN")
                # print(provider)
                scope.set_tag("integration_provider", provider)

                # it's possible for the link to be None, and in that
                # case it means we could not find a match for the
                # configuration
                if not result["sourceUrl"]:
                    matched_code_mappings.append(current_config)
                else:
                    found = True
                    # if we found a match, we can break
                    break

            # Post-processing before exiting scope context
            scope.set_tag("stacktrace_link.platform", ctx["platform"])
            scope.set_tag("stacktrace_link.found", found)
            if not found and matched_code_mappings:
                # For backwards compatibality we return the error of the last matched code mapping
                result["error"] = matched_code_mappings[-1]["error"]
                result["config"] = matched_code_mappings[-1]["config"]
                # Any code mapping that matches and its results will be returned
                result["matched_code_mappings"] = matched_code_mappings
                if result["error"] == "stack_root_mismatch":
                    scope.set_tag("stacktrace_link.error", "stack_root_mismatch")
                else:
                    scope.set_tag("stacktrace_link.error", "file_not_found")

        if result["config"]:
            analytics.record(
                "integration.stacktrace.linked",
                provider=result["config"]["provider"]["key"],
                config_id=result["config"]["id"],
                project_id=project.id,
                organization_id=project.organization_id,
                filepath=filepath,
                status=result.get("error") or "success",
            )

        return Response(result)
