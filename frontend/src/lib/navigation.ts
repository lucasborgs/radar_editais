export type NavigationDestination = {
  href: string;
  label: string;
  adminOnly?: boolean;
};

// Destinos estáveis da navegação do produto. Ícones e apresentação pertencem
// a cada shell; permissões e rotas ficam aqui para não divergirem.
export const PRIMARY_NAV_DESTINATIONS: readonly NavigationDestination[] = [
  { href: "/", label: "Consultor" },
  { href: "/radar", label: "Radar" },
  { href: "/projects", label: "Projetos" },
];

export const UTILITY_NAV_DESTINATIONS: readonly NavigationDestination[] = [
  { href: "/perfil", label: "Perfil" },
  { href: "/library", label: "Arquivos" },
  { href: "/pipeline", label: "Acompanhamento" },
  { href: "/oportunidades", label: "Ecossistema" },
  // Fila da Descoberta é ferramenta do operador (ADMIN_EMAILS), não do cliente.
  { href: "/discovered", label: "Descobertas", adminOnly: true },
];

export function visibleNavigationDestinations(
  destinations: readonly NavigationDestination[],
  isAdmin: boolean,
) {
  return destinations.filter((destination) => !destination.adminOnly || isAdmin);
}
