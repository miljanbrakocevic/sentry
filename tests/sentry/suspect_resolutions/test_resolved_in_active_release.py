from datetime import timedelta

from django.utils import timezone

from sentry.models import Deploy, Group, GroupRelease, GroupStatus
from sentry.suspect_resolutions.resolved_in_active_release import (
    is_resolved_issue_within_active_release,
)
from sentry.testutils import TestCase


class ResolvedInActiveReleaseTest(TestCase):
    def test_unresolved_issue_in_active_release(self):
        project = self.create_project()
        group = self.create_group(project=project, status=GroupStatus.UNRESOLVED)
        release = self.create_release(project=project)
        GroupRelease.objects.create(
            project_id=project.id,
            group_id=group.id,
            release_id=release.id,
        )
        Deploy.objects.create(
            organization_id=self.organization.id,
            environment_id=self.environment.id,
            release_id=release.id,
            date_finished=timezone.now() - timedelta(minutes=20),
        )

        assert not is_resolved_issue_within_active_release(group, project)

    def test_resolved_issue_in_active_release(self):
        project = self.create_project()
        group = Group.objects.create(status=GroupStatus.RESOLVED, project_id=project.id)
        release = self.create_release(project=project)
        GroupRelease.objects.create(
            project_id=project.id,
            group_id=group.id,
            release_id=release.id,
        )
        Deploy.objects.create(
            organization_id=self.organization.id,
            environment_id=self.environment.id,
            release_id=release.id,
            date_finished=timezone.now() - timedelta(minutes=20),
        )

        issue = Group.objects.filter(id=group.id, project_id=project.id)
        print(issue)
        assert GroupRelease.objects.filter(
            project_id=project.id, group_id=group.id, release_id=release.id
        )

        assert is_resolved_issue_within_active_release(group, project)

    def test_resolved_issue_in_old_deploy(self):
        project = self.create_project()
        group = self.create_group(project=project, status=GroupStatus.RESOLVED)
        release = self.create_release(project=project)
        GroupRelease.objects.create(
            project_id=project.id,
            group_id=group.id,
            release_id=release.id,
        )
        Deploy.objects.create(
            organization_id=self.organization.id,
            environment_id=self.environment.id,
            release_id=release.id,
            date_finished=timezone.now() - timedelta(days=3),
        )

        assert not is_resolved_issue_within_active_release(group, project)

    def test_resolved_issue_in_active_release_not_deployed(self):
        project = self.create_project()
        group = self.create_group(project=project, status=GroupStatus.RESOLVED)
        release = self.create_release(project=project)
        GroupRelease.objects.create(
            project_id=project.id,
            group_id=group.id,
            release_id=release.id,
        )
        assert not is_resolved_issue_within_active_release(group, project)
