import { deepEqual, rejects } from "node:assert/strict";
import { test } from "node:test";
import {
  artifactTypeForConsultantPath,
  saveAndConfirmConsultantProject,
} from "../src/lib/consultant-project";

test("saves before confirming and uses the saved revision", async () => {
  const calls: Array<[string, number]> = [];

  await saveAndConfirmConsultantProject({
    updates: { problem_hypothesis: "novo" },
    revision: 4,
    confirmation: {
      saveBrief: async (_updates, revision) => {
        calls.push(["save", revision]);
        return { revision: 5 };
      },
      confirmProject: async (revision) => {
        calls.push(["confirm", revision]);
      },
    },
  });

  deepEqual(calls, [["save", 4], ["confirm", 5]]);
});

test("does not confirm when saving fails", async () => {
  const calls: string[] = [];

  await rejects(() => saveAndConfirmConsultantProject({
    updates: { problem_hypothesis: "novo" },
    revision: 4,
    confirmation: {
      saveBrief: async () => {
        calls.push("save");
        throw new Error("save failed");
      },
      confirmProject: async () => {
        calls.push("confirm");
      },
    },
  }));

  deepEqual(calls, ["save"]);
});

test("opens market approach for non-formal consultant paths", () => {
  deepEqual(artifactTypeForConsultantPath({ formal_instrument: false }), "abordagem_mercado");
  deepEqual(artifactTypeForConsultantPath({ formal_instrument: true }), "proposta_tecnica");
});
