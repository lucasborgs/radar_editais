import { equal } from "node:assert/strict";
import { test } from "node:test";
import { modeFromEditalId, modeLabel } from "../src/components/workspace/types";
import { canCreateWorkspaceSession } from "../src/lib/workspace-policy";

test("labels investor-backed workspaces as legacy sessions", () => {
  equal(modeFromEditalId("investidor:acme"), "pitch");
  equal(modeLabel("pitch"), "Sessão legada");
  equal(canCreateWorkspaceSession("investidor:acme"), false);
});

test("keeps proposals in the active writing mode", () => {
  equal(modeFromEditalId("finep:123"), "proposal");
  equal(modeLabel("proposal"), "Proposta");
  equal(canCreateWorkspaceSession("finep:123"), true);
});

test("blocks retired investor targets before any new workspace is created", () => {
  equal(canCreateWorkspaceSession("investidor:seed-fund"), false);
});
