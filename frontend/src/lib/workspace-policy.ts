/** Determines whether a target may start a new active writing session. */
export function canCreateWorkspaceSession(editalId: string): boolean {
  return !editalId.startsWith("investidor:");
}
