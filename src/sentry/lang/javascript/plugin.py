from django.conf import settings

from sentry import options
from sentry.plugins.base.v2 import Plugin2
from sentry.stacktraces.processing import find_stacktraces_in_data
from sentry.utils.safe import get_path

from .errorlocale import translate_exception
from .errormapping import rewrite_exception
from .processor import JavaScriptStacktraceProcessor
from .processor_smcache import JavaScriptSmCacheStacktraceProcessor

PROCESSING_OPTION_SOURCEMAPCACHE = "processing.sourcemapcache-processor"


def preprocess_event(data):
    rewrite_exception(data)
    translate_exception(data)
    generate_modules(data)
    return data


def generate_modules(data):
    from sentry.lang.javascript.processor import generate_module

    for info in find_stacktraces_in_data(data):
        for frame in get_path(info.stacktrace, "frames", filter=True, default=()):
            platform = frame.get("platform") or data["platform"]
            if platform not in ("javascript", "node") or frame.get("module"):
                continue
            abs_path = frame.get("abs_path")
            if abs_path and abs_path.startswith(("http:", "https:", "webpack:", "app:")):
                frame["module"] = generate_module(abs_path)


# TODO(smcache): Remove after rollout.
def _use_sourcemapcache(project_id: int) -> bool:
    # Internal Sentry projects
    # 11276 - sentry/javascript project for forced dogfooding
    # SENTRY_PROJECT - default project for all installations
    # SENTRY_FRONTEND_PROJECT - configurable default frontend project
    if project_id in (11276, settings.SENTRY_PROJECT, settings.SENTRY_FRONTEND_PROJECT):
        return True

    return project_id % 1000 < options.get(PROCESSING_OPTION_SOURCEMAPCACHE, 0.0) * 1000


class JavascriptPlugin(Plugin2):
    can_disable = False

    def can_configure_for_project(self, project, **kwargs):
        return False

    def get_event_preprocessors(self, data, **kwargs):
        # XXX: rewrite_exception we probably also want if the event
        # platform is something else? unsure
        if data.get("platform") in ("javascript", "node"):
            return [preprocess_event]
        return []

    def get_stacktrace_processors(self, data, stacktrace_infos, platforms, **kwargs):
        if "javascript" in platforms or "node" in platforms:
            if _use_sourcemapcache(data["project"]):
                return [JavaScriptSmCacheStacktraceProcessor]
            return [JavaScriptStacktraceProcessor]
