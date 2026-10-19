import { util, extensions } from '@aws-appsync/utils';

export function request() {
  return { payload: null };
}

/**
 * Per-meeting live transcript access.
 *
 * onAddTranscriptSegment(CallId) is scoped to ONE meeting, so we check access
 * to that meeting IN CODE (no limit on project-list size) and then deliver its
 * segments with a single CallId filter. The old approach filtered segments by
 * `ProjectId in [myProjects]`, but segments carry no ProjectId, so that clause
 * matched nothing AND blew past AppSync's filter limits for users on many
 * projects - breaking the subscription entirely.
 */
export function response(ctx) {
  const { selectionSetList } = ctx.info;
  if (!selectionSetList.includes('Owner') || !selectionSetList.includes('SharedWith')) {
    console.error('You must include the "Owner" & "SharedWith" fields in the selection set');
    util.unauthorized();
  }

  const { groups } = ctx.identity;
  if (groups && groups.includes('Admin')) {
    return null; // Admin: AppSync applies the CallId argument filter implicitly.
  }

  const me = ctx.identity.username;
  const callId = ctx.args.CallId;

  if (callId && ctx.stash.callFound) {
    const owner = ctx.stash.callOwner;
    const shared = ctx.stash.callSharedWith || '';
    const projectId = ctx.stash.callProjectId;
    const excluded = ctx.stash.callExcluded || [];
    const myProjects = ctx.stash.myProjectIds || [];
    const allowed =
      owner === me ||
      shared.includes(me) ||
      (projectId && myProjects.includes(projectId) && !excluded.includes(me));
    if (!allowed) {
      util.unauthorized();
    }
    extensions.setSubscriptionFilter(
      util.transform.toSubscriptionFilter({ CallId: { eq: callId } })
    );
    return null;
  }

  // No CallId, or the call record does not exist yet: fall back to owner/shared
  // (bounded, safe - a stranger matches neither).
  extensions.setSubscriptionFilter(
    util.transform.toSubscriptionFilter({
      or: [{ Owner: { eq: me } }, { SharedWith: { contains: me } }],
    })
  );
  return null;
}
