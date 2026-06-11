import { redirect } from "next/navigation";

// Aposentado (spec_frontdoor_ux D7/§6): o dashboard deu lugar à porta única "/".
// Mantemos o diretório (redirect server-side, não delete) até a rota estabilizar.
export default function DashboardPage() {
  redirect("/");
}
