/*
 * Copyright (c) 2025 Amazon.com
 * This file is licensed under the MIT License.
 * See the LICENSE file in the project root for full license information.
 */
import { util } from '@aws-appsync/utils';

// Subscribe-time lookup of the caller's projects, stashed for the filter step.
export function request(ctx) {
  return {
    operation: 'GetItem',
    key: util.dynamodb.toMapValues({ PK: `u#${ctx.identity.username}` }),
  };
}

// Fail-open: any error or missing row -> empty list -> today's owner/sharedwith filter only.
export function response(ctx) {
  if (ctx.error) {
    ctx.stash.myProjectIds = [];
    return {};
  }
  ctx.stash.myProjectIds = (ctx.result && ctx.result.ProjectIds) ? ctx.result.ProjectIds : [];
  return {};
}
