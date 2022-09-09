import {useEffect, useRef} from 'react';
import {RouteComponentProps} from 'react-router';

import {switchOrganization} from 'sentry/actionCreators/organizations';
import OrganizationContextContainer, {
  OrganizationLegacyContext,
} from 'sentry/views/organizationContextContainer';

import Body from './body';

type Props = RouteComponentProps<{orgId: string}, {}> &
  Partial<React.ComponentProps<typeof OrganizationContextContainer>>;

function OrganizationDetails({children, ...props}: Props) {
  // Switch organizations when the orgId changes
  const orgId = useRef(props.params.orgId);
  const pathOrgId = OrganizationLegacyContext.getOrgIdFromHostOrParams(props);
  useEffect(() => {
    if (pathOrgId && orgId.current !== pathOrgId) {
      // Only switch on: org1 -> org2
      // Not on: undefined -> org1
      // Also avoid: org1 -> undefined -> org1
      if (orgId.current) {
        switchOrganization();
      }

      orgId.current = props.params.orgId;
    }
  }, [pathOrgId, props.params.orgId]);

  return (
    <OrganizationContextContainer includeSidebar useLastOrganization {...props}>
      <Body>{children}</Body>
    </OrganizationContextContainer>
  );
}

export default OrganizationDetails;
