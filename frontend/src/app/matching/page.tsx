import { redirect } from "next/navigation";

// Aposentado (spec_frontdoor_ux D7/§6): a tela de matching deu lugar ao radar
// inline na conversa do front-door "/". Mantemos o diretório (redirect
// server-side, não delete) até a rota estabilizar.
export default function MatchingPage() {
  redirect("/");
}
