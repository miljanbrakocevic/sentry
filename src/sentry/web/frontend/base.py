import logging
from typing import List, NoReturn, Optional, Tuple

from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotFound,
    HttpResponseRedirect,
)
from django.middleware.csrf import CsrfViewMiddleware
from django.template.context_processors import csrf
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View
from rest_framework.request import Request
from rest_framework.response import Response

from sentry.api.serializers import serialize
from sentry.api.utils import generate_organization_url, is_member_disabled_from_limit
from sentry.auth import access
from sentry.auth.superuser import is_active_superuser
from sentry.models import Authenticator, Organization, Project, ProjectStatus, Team, TeamStatus
from sentry.services.hybrid_cloud.organization import (
    ApiOrganization,
    ApiUserOrganizationContext,
    organization_service,
)
from sentry.silo import SiloMode
from sentry.utils import auth
from sentry.utils.audit import create_audit_entry
from sentry.utils.auth import is_valid_redirect, make_login_link_with_redirect
from sentry.utils.http import absolute_uri, is_using_customer_domain
from sentry.web.frontend.generic import FOREVER_CACHE
from sentry.web.helpers import render_to_response
from sudo.views import redirect_to_sudo

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("sentry.audit.ui")


class OrganizationMixin:
    # This attribute will only be set once determine_active_organization is called.  Subclasses should likely invoke
    # that method, passing along the organization_slug context that might exist (or might not).
    active_organization: Optional[ApiUserOrganizationContext]

    # TODO(dcramer): move the implicit organization logic into its own class
    # as it's only used in a single location and over complicates the rest of
    # the code
    def determine_active_organization(self, request: Request, organization_slug=None) -> NoReturn:
        """
        Using the current request and potentially optional organization_slug, 'determines'
        the current session for this mixin object's scope, placing it into the active_organization attribute.

        Generally this method only need be called once at the head of a request, as it can potentially have side
        effects in the user's session.  That said, when login occurs during a request, this method should be called
        be called again to refresh an active organization context.
        """

        if organization_slug is None:
            is_implicit = True
            organization_slug = self._find_implicit_slug(request)
        else:
            is_implicit = False

        active_organization, backup_organization = self._lookup_organizations(
            is_implicit, organization_slug, request
        )

        if active_organization is None and backup_organization:
            if not is_implicit:
                self.active_organization = None
                return
            active_organization = ApiUserOrganizationContext(
                user_id=request.user.id,
                organization=backup_organization,
                member=organization_service.check_membership_by_id(
                    organization_id=backup_organization.id, user_id=request.user.id
                ),
            )

        if active_organization and active_organization.member:
            auth.set_active_org(request, active_organization.organization.slug)

        self.active_organization = active_organization

    def _lookup_organizations(
        self, is_implicit: bool, organization_slug: Optional[str], request: Request
    ) -> Tuple[Optional[ApiUserOrganizationContext], Optional[ApiOrganization]]:
        active_organization: Optional[ApiUserOrganizationContext] = self._try_superuser_org_lookup(
            organization_slug, request
        )
        backup_organization: Optional[ApiOrganization] = None
        if active_organization is None:
            organizations: List[ApiOrganization]
            organizations = organization_service.get_organizations(
                user_id=request.user.id, scope=None, only_visible=True
            )

            if organizations:
                backup_organization = organizations[0]
                if organization_slug:
                    active_organization = self._try_finding_org_from_slug(
                        is_implicit, organization_slug, organizations, request
                    )
        return active_organization, backup_organization

    def _try_finding_org_from_slug(
        self,
        is_implicit: bool,
        organization_slug: str,
        organizations: List[ApiOrganization],
        request: Request,
    ) -> Optional[ApiUserOrganizationContext]:
        try:
            backup_org: Optional[ApiOrganization] = next(
                o for o in organizations if o.slug == organization_slug
            )
        except StopIteration:
            logger.info("Active organization [%s] not found in scope", organization_slug)
            if is_implicit:
                session = request.session
                if session and "activeorg" in session:
                    del session["activeorg"]
            backup_org = None

        if backup_org is not None:
            membership = organization_service.check_membership_by_id(
                organization_id=backup_org.id, user_id=request.user.id
            )
            return ApiUserOrganizationContext(
                user_id=request.user.id, organization=backup_org, member=membership
            )
        return None

    def _try_superuser_org_lookup(
        self, organization_slug: str, request: Request
    ) -> Optional[ApiUserOrganizationContext]:
        active_organization: Optional[ApiUserOrganizationContext] = None
        if organization_slug is not None:
            if is_active_superuser(request):
                active_organization = organization_service.get_organization_by_slug(
                    user_id=request.user.id, slug=organization_slug, only_visible=True
                )
        return active_organization

    def _find_implicit_slug(self, request):
        organization_slug = request.session.get("activeorg")
        if request.subdomain is not None and request.subdomain != organization_slug:
            # Customer domain is being used, set the subdomain as the requesting org slug.
            organization_slug = request.subdomain
        return organization_slug

    def is_not_2fa_compliant(self, request: Request, organization):
        return (
            organization.flags.require_2fa
            and not Authenticator.objects.user_has_2fa(request.user)
            and not is_active_superuser(request)
        )

    def is_member_disabled_from_limit(self, request: Request, organization):
        return is_member_disabled_from_limit(request, organization)

    def get_active_team(self, request: Request, organization, team_slug):
        """
        Returns the currently selected team for the request or None
        if no match.
        """
        try:
            team = Team.objects.get_from_cache(slug=team_slug, organization=organization)
        except Team.DoesNotExist:
            return None

        if team.status != TeamStatus.VISIBLE:
            return None

        return team

    def get_active_project(self, request: Request, organization, project_slug):
        try:
            project = Project.objects.get(slug=project_slug, organization=organization)
        except Project.DoesNotExist:
            return None

        if project.status != ProjectStatus.VISIBLE:
            return None

        return project

    def redirect_to_org(self, request: Request):
        from sentry import features

        using_customer_domain = request and is_using_customer_domain(request)

        # TODO(dcramer): deal with case when the user cannot create orgs
        if self.active_organization:
            current_org_slug = self.active_organization.organization.slug
            url = Organization.get_url(current_org_slug)
        elif not features.has("organizations:create"):
            return self.respond("sentry/no-organization-access.html", status=403)
        else:
            org_exists = False
            url = "/organizations/new/"
            if using_customer_domain:
                url = absolute_uri(url)

            if using_customer_domain and request.user and request.user.is_authenticated:
                organizations = organization_service.get_organizations(
                    user_id=request.user.id, scope=None, only_visible=True
                )
                org_exists = (
                    organization_service.check_organization_by_slug(
                        slug=request.subdomain, only_visible=True
                    )
                    is not None
                )
                if org_exists and organizations:
                    url = reverse("sentry-auth-organization", args=[request.subdomain])
                    url_prefix = generate_organization_url(request.subdomain)
                    url = absolute_uri(url, url_prefix=url_prefix)

        return HttpResponseRedirect(url)


class BaseView(View, OrganizationMixin):
    auth_required = True
    # TODO(dcramer): change sudo so it can be required only on POST
    sudo_required = False

    csrf_protect = True

    def __init__(self, auth_required=None, sudo_required=None, csrf_protect=None, *args, **kwargs):
        if auth_required is not None:
            self.auth_required = auth_required
        if sudo_required is not None:
            self.sudo_required = sudo_required
        if csrf_protect is not None:
            self.csrf_protect = csrf_protect
        super().__init__(*args, **kwargs)

    @csrf_exempt
    def dispatch(self, request, *args, **kwargs):
        """
        A note on the CSRF protection process.

        Because the CSRF decorators don't work well with view subclasses, we
        allow them to control whether a CSRF check is done by setting
        self.csrf_protect. This has a couple of implications:

        1. We need to mark this method as @csrf_exempt so that when the CSRF
           middleware checks it as part of the regular middleware sequence, it
           always passes.
        2. If self.csrf_protect is set, we will re-run the CSRF check ourselves
           using CsrfViewMiddleware().process_view()
        3. But first we must remove the csrf_exempt attribute that was set by
           the decorator so that the middleware doesn't shortcut and pass the
           check unconditionally again.

        """

        organization_slug = kwargs.get("organization_slug", None)
        if request and is_using_customer_domain(request):
            organization_slug = request.subdomain
        self.determine_active_organization(request, organization_slug)

        if self.csrf_protect:
            if hasattr(self.dispatch.__func__, "csrf_exempt"):
                delattr(self.dispatch.__func__, "csrf_exempt")
            response = self.test_csrf(request)
            if response:
                return response

        if self.is_auth_required(request, *args, **kwargs):
            return self.handle_auth_required(request, *args, **kwargs)

        if self.is_sudo_required(request, *args, **kwargs):
            return self.handle_sudo_required(request, *args, **kwargs)

        args, kwargs = self.convert_args(request, *args, **kwargs)

        request.access = self.get_access(request, *args, **kwargs)

        if not self.has_permission(request, *args, **kwargs):
            return self.handle_permission_required(request, *args, **kwargs)

        if "organization" in kwargs:
            org = kwargs["organization"]
            if self.is_member_disabled_from_limit(request, org):
                return self.handle_disabled_member(org)
            if self.is_not_2fa_compliant(request, org):
                return self.handle_not_2fa_compliant(request, *args, **kwargs)

        self.request = request
        self.default_context = self.get_context_data(request, *args, **kwargs)

        return self.handle(request, *args, **kwargs)

    def test_csrf(self, request: Request):
        middleware = CsrfViewMiddleware()
        return middleware.process_view(request, self.dispatch, [request], {})

    def get_access(self, request: Request, *args, **kwargs):
        return access.DEFAULT

    def convert_args(self, request: Request, *args, **kwargs):
        return (args, kwargs)

    def handle(self, request: Request, *args, **kwargs) -> Response:
        return super().dispatch(request, *args, **kwargs)

    def is_auth_required(self, request: Request, *args, **kwargs):
        return self.auth_required and not (request.user.is_authenticated and request.user.is_active)

    def handle_auth_required(self, request: Request, *args, **kwargs):
        auth.initiate_login(request, next_url=request.get_full_path())
        if "organization_slug" in kwargs:
            redirect_to = reverse("sentry-auth-organization", args=[kwargs["organization_slug"]])
        else:
            redirect_to = auth.get_login_url()
        return self.redirect(redirect_to, headers={"X-Robots-Tag": "noindex, nofollow"})

    def is_sudo_required(self, request: Request, *args, **kwargs):
        return self.sudo_required and not request.is_sudo()

    def handle_sudo_required(self, request: Request, *args, **kwargs):
        return redirect_to_sudo(request.get_full_path())

    def has_permission(self, request: Request, *args, **kwargs):
        return True

    def handle_permission_required(self, request: Request, *args, **kwargs):
        redirect_uri = self.get_no_permission_url(request, *args, **kwargs)
        return self.redirect(redirect_uri)

    def handle_not_2fa_compliant(self, request: Request, *args, **kwargs):
        redirect_uri = self.get_not_2fa_compliant_url(request, *args, **kwargs)
        return self.redirect(redirect_uri)

    def get_no_permission_url(self, request: Request, *args, **kwargs):
        return reverse("sentry-login")

    def get_not_2fa_compliant_url(self, request: Request, *args, **kwargs):
        return reverse("sentry-account-settings-security")

    def get_context_data(self, request: Request, **kwargs):
        context = csrf(request)
        return context

    def respond(self, template, context=None, status=200):
        default_context = self.default_context
        if context:
            default_context.update(context)

        return render_to_response(template, default_context, self.request, status=status)

    def redirect(self, url, headers=None):
        res = HttpResponseRedirect(url)
        if headers:
            for k, v in headers.items():
                res[k] = v
        return res

    def get_team_list(self, user, organization):
        return Team.objects.get_for_user(organization=organization, user=user, with_projects=True)

    def create_audit_entry(self, request: Request, transaction_id=None, **kwargs):
        return create_audit_entry(request, transaction_id, audit_logger, **kwargs)

    def handle_disabled_member(self, organization):
        redirect_uri = reverse("sentry-organization-disabled-member", args=[organization.slug])
        return self.redirect(redirect_uri)


class OrganizationView(BaseView):
    """
    Any view acting on behalf of an organization should inherit from this base.

    The 'organization' keyword argument is automatically injected into the
    resulting dispatch.
    """

    required_scope = None
    valid_sso_required = True

    def get_access(self, request: Request, organization, *args, **kwargs):
        if organization is None:
            return access.DEFAULT
        return access.from_request(request, organization)

    def get_context_data(self, request: Request, organization, **kwargs):
        context = super().get_context_data(request)
        context["organization"] = organization
        context["TEAM_LIST"] = self.get_team_list(request.user, organization)
        context["ACCESS"] = request.access.to_django_context()
        return context

    def has_permission(self, request: Request, organization, *args, **kwargs):
        if organization is None:
            return False
        if self.valid_sso_required:
            if request.access.requires_sso and not request.access.sso_is_valid:
                return False
            if self.needs_sso(request, organization):
                return False
        if self.required_scope and not request.access.has_scope(self.required_scope):
            logger.info(
                "User %s does not have %s permission to access organization %s",
                request.user,
                self.required_scope,
                organization,
            )
            return False
        return True

    def is_auth_required(self, request: Request, organization_slug=None, *args, **kwargs):
        result = super().is_auth_required(request, *args, **kwargs)
        if result:
            return result

        # if the user is attempting to access an organization that *may* be
        # accessible if they simply re-authenticate, we want to allow that
        # this opens up a privacy hole, but the pros outweigh the cons
        if not organization_slug:
            return False

        if not self.active_organization:
            # Require auth if we there is an organization associated with the slug that we just cannot access
            # for some reason.
            return (
                organization_service.get_organization_by_slug(
                    user_id=None, slug=organization_slug, only_visible=True
                )
                is not None
            )

        return False

    def handle_permission_required(self, request: Request, organization, *args, **kwargs):
        if self.needs_sso(request, organization):
            logger.info(
                "access.must-sso",
                extra={"organization_id": organization.id, "user_id": request.user.id},
            )
            auth.initiate_login(request)
            path = reverse("sentry-auth-organization", args=[organization.slug])

            request_path = request.get_full_path()

            after_login_redirect = (
                request_path
                if is_valid_redirect(request_path, allowed_hosts=(request.get_host(),))
                else None
            )
            redirect_uri = make_login_link_with_redirect(path, after_login_redirect)

        else:
            redirect_uri = self.get_no_permission_url(request, *args, **kwargs)
        return self.redirect(redirect_uri)

    def needs_sso(self, request: Request, organization):
        if not organization:
            return False
        # XXX(dcramer): this branch should really never hit
        if not request.user.is_authenticated:
            return False
        if not self.valid_sso_required:
            return False
        if not request.access.requires_sso:
            return False
        if not auth.has_completed_sso(request, organization.id):
            return True
        if not request.access.sso_is_valid:
            return True
        return False

    def convert_args(self, request: Request, organization_slug=None, *args, **kwargs):
        # TODO:  Extract separate view base classes based on control vs region / monolith,
        # with distinct convert_args implementation.
        if SiloMode.get_current_mode() == SiloMode.CONTROL:
            kwargs["organization"] = self.active_organization.organization
        else:
            organization: Optional[Organization] = None
            if self.active_organization:
                for org in Organization.objects.filter(id=self.active_organization.organization.id):
                    organization = org

            kwargs["organization"] = organization

        return (args, kwargs)


class ProjectView(OrganizationView):
    """
    Any view acting on behalf of a project should inherit from this base and the
    matching URL pattern must pass 'org_slug' as well as 'project_slug'.

    Three keyword arguments are added to the resulting dispatch:

    - organization
    - project
    """

    def get_context_data(self, request: Request, organization, project, **kwargs):
        context = super().get_context_data(request, organization)
        context["project"] = project
        context["processing_issues"] = serialize(project).get("processingIssues", 0)
        return context

    def has_permission(self, request: Request, organization, project, *args, **kwargs):
        if project is None:
            return False
        rv = super().has_permission(request, organization)
        if not rv:
            return rv

        teams = list(project.teams.all())

        if self.required_scope:
            if not any(request.access.has_team_scope(team, self.required_scope) for team in teams):
                logger.info(
                    "User %s does not have %s permission to access project %s",
                    request.user,
                    self.required_scope,
                    project,
                )
                return False
        elif not any(request.access.has_team_access(team) for team in teams):
            logger.info("User %s does not have access to project %s", request.user, project)
            return False
        return True

    def convert_args(self, request: Request, organization_slug, project_slug, *args, **kwargs):
        organization: Optional[Organization] = None
        active_project: Optional[Project] = None
        if self.active_organization:
            for org in Organization.objects.filter(id=self.active_organization.organization.id):
                organization = org

            if organization:
                active_project = self.get_active_project(
                    request=request, organization=organization, project_slug=project_slug
                )
        else:
            active_project = None

        kwargs["project"] = active_project
        kwargs["organization"] = organization

        return (args, kwargs)


class AvatarPhotoView(View):
    model = None

    def get(self, request: Request, *args, **kwargs) -> Response:
        avatar_id = kwargs["avatar_id"]
        try:
            avatar = self.model.objects.get(ident=avatar_id)
        except self.model.DoesNotExist:
            return HttpResponseNotFound()

        photo = avatar.get_file()
        if not photo:
            return HttpResponseNotFound()

        size = request.GET.get("s")
        photo_file = photo.getfile()
        if size:
            try:
                size = int(size)
            except ValueError:
                return HttpResponseBadRequest()
            else:
                photo_file = avatar.get_cached_photo(size)

        res = HttpResponse(photo_file, content_type="image/png")
        res["Cache-Control"] = FOREVER_CACHE
        return res
