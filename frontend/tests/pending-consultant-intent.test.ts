import { equal } from "node:assert/strict";
import { test } from "node:test";
import { savePendingConsultantIntent, takePendingConsultantIntent } from "../src/lib/pending-consultant-intent";

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    removeItem: (key: string) => values.delete(key),
    setItem: (key: string, value: string) => values.set(key, value),
  };
}

test("preserves the anonymous intent until the consultant resumes it", () => {
  const storage = memoryStorage();
  savePendingConsultantIntent(storage, "  Quero encontrar um edital para P&D  ");

  equal(takePendingConsultantIntent(storage), "Quero encontrar um edital para P&D");
  equal(takePendingConsultantIntent(storage), null);
});
