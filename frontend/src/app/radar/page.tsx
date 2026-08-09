import { redirect } from "next/navigation";

/**
 * O Radar antigo foi absorvido pelo ConsultantGraph. Mantemos o deep-link
 * durante a migração, sem executar ranking ou criar escrita fora de um caminho.
 */
export default function RadarPage() {
  redirect("/");
}
