import {RouteComponentProps} from 'react-router';
import {Location, LocationDescriptor, LocationDescriptorObject} from 'history';

import {initializeOrg} from 'sentry-test/initializeOrg';
import {render, screen} from 'sentry-test/reactTestingLibrary';

import withDomainRequired, {normalizeUrl} from 'sentry/utils/withDomainRequired';

describe('normalizeUrl', function () {
  beforeEach(function () {
    window.__initialData = {
      customerDomain: {
        subdomain: 'albertos-apples',
        organizationUrl: 'https://albertos-apples.sentry.io',
        sentryUrl: 'https://sentry.io',
      },
    } as any;
  });

  it('replaces paths in strings', function () {
    const location = TestStubs.location();
    let result = normalizeUrl('/settings/organization', location);
    expect(result).toEqual('/settings/organization');

    result = normalizeUrl('/settings/sentry/members', location);
    expect(result).toEqual('/settings/members');

    result = normalizeUrl('/organizations/albertos-apples/issues', location);
    expect(result).toEqual('/issues');

    result = normalizeUrl('/organizations/albertos-apples/issues?_q=all', location);
    expect(result).toEqual('/issues?_q=all');
  });

  it('replaces pathname in objects', function () {
    const location = TestStubs.location();
    let result = normalizeUrl({pathname: '/settings/organization'}, location);
    // @ts-ignore
    expect(result.pathname).toEqual('/settings/organization');

    result = normalizeUrl({pathname: '/settings/sentry/members'}, location);
    // @ts-ignore
    expect(result.pathname).toEqual('/settings/members');

    result = normalizeUrl({pathname: '/organizations/albertos-apples/issues'}, location);
    // @ts-ignore
    expect(result.pathname).toEqual('/issues');

    result = normalizeUrl(
      {
        pathname: '/organizations/albertos-apples/issues',
        query: {q: 'all'},
      },
      location
    );
    // @ts-ignore
    expect(result.pathname).toEqual('/issues');
  });

  it('replaces pathname in function callback', function () {
    const location = TestStubs.location();
    function objectCallback(_loc: Location): LocationDescriptorObject {
      return {pathname: '/settings/organization'};
    }
    let result = normalizeUrl(objectCallback, location);
    // @ts-ignore
    expect(result.pathname).toEqual('/settings/organization');

    function stringCallback(_loc: Location): LocationDescriptor {
      return '/organizations/a-long-slug/discover/';
    }
    result = normalizeUrl(stringCallback, location);
    expect(result).toEqual('/discover/');
  });
});

describe('withDomainRequired', function () {
  type Props = RouteComponentProps<{orgId: string}, {}>;
  const MyComponent = (props: Props) => {
    const {params} = props;
    return <div>Org slug: {params.orgId ?? 'no org slug'}</div>;
  };

  beforeEach(function () {
    Object.defineProperty(window, 'location', {
      writable: true,
      value: {
        replace: jest.fn(),
        pathname: '/organizations/albertos-apples/issues/',
        search: '?q=123',
        hash: '#hash',
      },
    });
    window.__initialData = {
      customerDomain: {
        subdomain: 'albertos-apples',
        organizationUrl: 'https://albertos-apples.sentry.io',
        sentryUrl: 'https://sentry.io',
      },
      links: {
        organizationUrl: null,
        regionUrl: null,
        sentryUrl: 'https://sentry.io',
      },
    } as any;
  });

  it('redirects to sentryUrl in non-customer domain world', function () {
    window.__initialData = {
      customerDomain: null,
      features: ['organizations:customer-domains'],
      links: {
        organizationUrl: null,
        regionUrl: null,
        sentryUrl: 'https://sentry.io',
      },
    } as any;

    const organization = TestStubs.Organization({
      slug: 'albertos-apples',
      features: [],
    });

    const params = {
      orgId: 'albertos-apples',
    };
    const {router, route, routerContext} = initializeOrg({
      ...initializeOrg(),
      organization,
      router: {
        params,
      },
    });
    const WrappedComponent = withDomainRequired(MyComponent);
    const {container} = render(
      <WrappedComponent
        router={router}
        location={router.location}
        params={params}
        routes={router.routes}
        routeParams={router.params}
        route={route}
      />,
      {context: routerContext}
    );

    expect(container).toBeEmptyDOMElement();
    expect(window.location.replace).toHaveBeenCalledTimes(1);
    expect(window.location.replace).toHaveBeenCalledWith(
      'https://sentry.io/organizations/albertos-apples/issues/?q=123#hash'
    );
  });

  it('redirects to sentryUrl if customer-domains is omitted', function () {
    window.__initialData = {
      customerDomain: {
        subdomain: 'albertos-apples',
        organizationUrl: 'https://albertos-apples.sentry.io',
        sentryUrl: 'https://sentry.io',
      },
      features: [],
      links: {
        organizationUrl: null,
        regionUrl: null,
        sentryUrl: 'https://sentry.io',
      },
    } as any;

    const organization = TestStubs.Organization({
      slug: 'albertos-apples',
      features: [],
    });

    const params = {
      orgId: 'albertos-apples',
    };
    const {router, route, routerContext} = initializeOrg({
      ...initializeOrg(),
      organization,
      router: {
        params,
      },
    });
    const WrappedComponent = withDomainRequired(MyComponent);
    const {container} = render(
      <WrappedComponent
        router={router}
        location={router.location}
        params={params}
        routes={router.routes}
        routeParams={router.params}
        route={route}
      />,
      {context: routerContext}
    );

    expect(container).toBeEmptyDOMElement();
    expect(window.location.replace).toHaveBeenCalledTimes(1);
    expect(window.location.replace).toHaveBeenCalledWith(
      'https://sentry.io/organizations/albertos-apples/issues/?q=123#hash'
    );
  });

  it('renders when window.__initialData.customerDomain and customer-domains feature is present', function () {
    window.__initialData = {
      customerDomain: {
        subdomain: 'albertos-apples',
        organizationUrl: 'https://albertos-apples.sentry.io',
        sentryUrl: 'https://sentry.io',
      },
      features: ['organizations:customer-domains'],
      links: {
        organizationUrl: 'https://albertos-apples.sentry.io',
        regionUrl: 'https://eu.sentry.io',
        sentryUrl: 'https://sentry.io',
      },
    } as any;

    const organization = TestStubs.Organization({
      slug: 'albertos-apples',
      features: [],
    });

    const params = {
      orgId: 'albertos-apples',
    };
    const {router, route, routerContext} = initializeOrg({
      ...initializeOrg(),
      organization,
      router: {
        params,
      },
    });
    const WrappedComponent = withDomainRequired(MyComponent);
    render(
      <WrappedComponent
        router={router}
        location={router.location}
        params={params}
        routes={router.routes}
        routeParams={router.params}
        route={route}
      />,
      {context: routerContext}
    );

    expect(screen.getByText('Org slug: albertos-apples')).toBeInTheDocument();
    expect(window.location.replace).toHaveBeenCalledTimes(0);
  });
});
