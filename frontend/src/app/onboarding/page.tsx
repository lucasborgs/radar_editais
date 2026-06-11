import { redirect } from "next/navigation";

// Aposentado (spec_frontdoor_ux D7/§6): o onboarding por formulário foi
// substituído pela construção de perfil na própria conversa do front-door "/".
// Mantemos o diretório (redirect server-side, não delete) até a rota estabilizar.
export default function OnboardingPage() {
  redirect("/");
}
