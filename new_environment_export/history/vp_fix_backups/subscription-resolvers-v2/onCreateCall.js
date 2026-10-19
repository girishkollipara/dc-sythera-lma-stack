import { util, extensions } from '@aws-appsync/utils';

export function request() {
  return { payload: null };
}

/**
 * onCreateCall is global (no CallId) and fires when a meeting is first created -
 * BEFORE any ProjectId is assigned (tagging happens later). Project-based live
 * notification is therefore not possible here; project members see new meetings
 * on refresh. Filter by owner/shared only, which is bounded and never exceeds
 * AppSync's subscription filter limits.
 */
export function response(ctx) {
  const { selectionSetList } = ctx.info;
  if (!selectionSetList.includes('Owner') || !selectionSetList.includes('SharedWith')) {
    console.error('You must include the "Owner" & "SharedWith" fields in the selection set');
    util.unauthorized();
  }
  const { groups } = ctx.identity;
  if (groups && groups.includes('Admin')) {
    return null;
  }
  const me = ctx.identity.username;
  extensions.setSubscriptionFilter(
    util.transform.toSubscriptionFilter({
      or: [{ Owner: { eq: me } }, { SharedWith: { contains: me } }],
    })
  );
  return null;
}
