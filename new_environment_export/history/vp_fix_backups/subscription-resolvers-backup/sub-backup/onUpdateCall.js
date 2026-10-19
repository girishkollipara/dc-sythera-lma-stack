/*
 * Copyright (c) 2025 Amazon.com
 * This file is licensed under the MIT License.
 * See the LICENSE file in the project root for full license information.
 */
export function request() {
  return { payload: null };
}

/**
 * @param {import('@aws-appsync/utils').Context} ctx the context
 * @returns {*} the request
 */
export function response(ctx) {
  const { fieldName, parentTypeName, selectionSetGraphQL, selectionSetList, variables } = ctx.info;

  console.debug(`/*****/`);
  console.debug(`setting up subscription for user ${ctx.identity.username}`);
  console.debug(`fieldName: ${fieldName}`);
  console.debug(`parentTypeName: ${parentTypeName}`);
  console.debug(`selectionSetGraphQL: ${selectionSetGraphQL}`);
  console.debug(`selectionSetList: ${selectionSetList}`);
  console.debug(`variables: ${variables}`);
  console.debug(`/*****/`);

  if (!selectionSetList.includes('Owner') || !selectionSetList.includes('SharedWith')) {
    console.error('You must included the "Owner" & "SharedWith fields in the selection set');
    util.unauthorized();
  }

  const { groups } = ctx.identity;
  if((groups === undefined) || !ctx.identity.groups.includes("Admin")) {
    console.debug(`Setting subscription filter with Owner or User that have been give access`);
    const me = ctx.identity.username;
    const myProjects = ctx.stash.myProjectIds || [];
    const orRules = [
      { Owner: { eq: me } },
      { SharedWith: { contains: me } }
    ];
    // ADDED: also deliver live events for meetings in the caller's projects.
    // (Per-meeting block is enforced on the read/refresh path; the live filter stays simple.)
    if (myProjects.length > 0) {
      orRules.push({ ProjectId: { in: myProjects } });
    }
    extensions.setSubscriptionFilter(util.transform.toSubscriptionFilter({ or: orRules }));
  }
  return null;
}
