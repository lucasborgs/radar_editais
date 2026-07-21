# Design System — Radar de Editais

## Stack

- **Next.js 14** (App Router) + **TypeScript** + **Tailwind CSS 3.4**
- **Radix UI** (headless) para primitivos de UI
- **Recharts 3.7** para gráficos
- **clsx + tailwind-merge** para composição de classes

---

## Paleta de Cores

### Tokens principais

| Token Tailwind | Valor | Uso |
|---|---|---|
| `primary` | `#1DB954` | Verde Spotify — CTAs, active states, foco |
| `primary-hover` | `#1ED760` | Hover do primário |
| `app-bg` | `#F9FAFB` | Fundo da aplicação |
| `surface` | `#FFFFFF` | Cards, painéis |
| `content-primary` | `#111827` | Texto principal |
| `content-secondary` | `#6B7280` | Texto secundário, labels |
| `border` | `#E5E7EB` | Bordas gerais |

### Fontes por fonte de dados

Usadas em gráficos e dots de filtro (`src/lib/constants.ts`):

| Fonte | Cor |
|---|---|
| FAPESP | `#4f86c6` |
| FINEP | `#e07b39` |
| BNDES | `#2e7d32` |
| CNPq | `#7c3aed` |
| Arapyau | `#b45309` |
| Serrapilheira | `#0e7490` |
| Lemann | `#db2777` |
| Itaú Social | `#dc2626` |

### Status badges

Background com transparência via Tailwind (`color/15`):

| Status | Background | Texto |
|---|---|---|
| ABERTA | `bg-[#1DB954]/15` | `text-[#169c46]` |
| ENCERRADA | `bg-gray-500/15` | `text-gray-600` |
| FLUXO_CONTINUO | `bg-amber-500/15` | `text-amber-700` |
| VERIFICAR | `bg-blue-500/15` | `text-blue-700` |

### Aderência (matching score)

| Nível | Background | Texto | Cor do score |
|---|---|---|---|
| ALTA_ADERENCIA | `bg-[#1DB954]/15` | `text-[#169c46]` | `#1DB954` |
| MEDIA_ADERENCIA | `bg-amber-500/15` | `text-amber-700` | `#f59e0b` |
| BAIXA_ADERENCIA | `bg-orange-500/15` | `text-orange-700` | `#f97316` |
| INCOMPATIVEL | `bg-red-500/15` | `text-red-700` | `#ef4444` |

---

## Tipografia

### Famílias de fontes

| Classe Tailwind | Família | Fonte | Uso |
|---|---|---|---|
| `font-heading` | **Cabinet Grotesk Bold** | Fontshare CDN | Todos os headings (h1–h6) |
| `font-sans` | **Inter** 400/500/600 | Google Fonts | Corpo, labels, UI copy |
| `font-mono` / `.font-data` | **Fragment Mono** | Google Fonts | Números, IDs, métricas |

### CSS variables injetadas pelo Next.js

```css
--font-cabinet        /* Cabinet Grotesk */
--font-inter          /* Inter */
--font-fragment-mono  /* Fragment Mono */
```

### Hierarquia tipográfica

```
Headings (Cabinet Grotesk, font-weight: 700)
  h1–h6: font-family: var(--font-cabinet)

Corpo (Inter)
  Base:    1rem / 400     (16px)
  Labels:  0.875rem / 400–600  (14px)
  Caption: 0.75rem / 400–600   (12px)
  Micro:   0.625rem / 600      (10px, uppercase + tracking-wider)

Dados numéricos (Fragment Mono)
  Métricas: text-3xl / font-normal
  Tabelas:  text-sm–xs
  IDs:      text-xs
  Sempre com: font-variant-numeric: tabular-nums
```

### Utility `.font-data`

```css
@layer utilities {
  .font-data {
    font-family: var(--font-fragment-mono), ui-monospace, monospace;
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum";
  }
}
```

---

## Bordas, Raio e Sombras

### Border radius

| Classe | Valor | Uso |
|---|---|---|
| `rounded-lg` | 8px | Inputs, botões |
| `rounded-xl` | 12px | Cards padrão |
| `rounded-2xl` | 16px | Containers maiores |
| `rounded-full` | 9999px | Badges, pills |

### Sombras

| Token | Valor | Uso |
|---|---|---|
| `shadow-sm` | `0 1px 2px 0 rgb(0 0 0 / 0.05)` | Elevação mínima |
| `shadow-card` | `0 1px 3px 0 rgb(0 0 0 / 0.08), 0 1px 2px -1px rgb(0 0 0 / 0.06)` | Cards, painéis |

---

## Layout & Espaçamento

### Estrutura de página

```
┌─ AppSidebar (w-64, 256px) ─┬─ Main ───────────────────────────┐
│                             │  Header h-14 (56px) border-b     │
│  Logo px-5 py-5             │─────────────────────────────────  │
│  Nav links px-3 py-4        │  Content p-6                     │
│  Footer px-5 py-4           │    ├─ FilterSidebar w-64         │
│                             │    └─ flex-1 min-w-0             │
└─────────────────────────────┴──────────────────────────────────┘
```

### Valores de referência

| Elemento | Classe | Valor |
|---|---|---|
| Sidebar width | `w-64` | 256px |
| Header height | `h-14` | 56px |
| Padding de conteúdo | `p-6` | 24px |
| Padding interno de card | `p-5` | 20px |
| Gap entre cards | `gap-4` a `gap-6` | 16–24px |
| Espaçamento vertical em filtros | `space-y-5` | 20px |

### Grids responsivos

```
KPIs:    grid-cols-2 gap-4 lg:grid-cols-4
Gráficos: grid-cols-1 gap-4 lg:grid-cols-2
```

---

## Componentes

### MetricCard (`src/components/ui/MetricCard.tsx`)

```
┌─ barra verde h-1 (bg-primary) ────────────────────────────────┐
│  LABEL UPPERCASE XS SEMIBOLD TRACKING-WIDER                   │
│  123.456  ← font-data text-3xl                                │
│  [+12% ↑]  ← badge rounded-full text-xs                       │
└───────────────────────────────────────────────────────────────┘
Classes: bg-white rounded-xl shadow-card border border-border p-5 pt-6
```

### DataTable (`src/components/ui/DataTable.tsx`)

- Container: `rounded-xl border border-border bg-white`
- Header: `bg-gray-50 border-b border-border`
- Cabeçalhos: `uppercase text-xs font-semibold tracking-wider text-content-secondary`
- Células: `px-4 py-3.5`
- Hover em linha clicável: `hover:bg-gray-50 transition-colors`
- Colunas numéricas: `.font-data`
- Loading: skeleton `h-4 bg-gray-200 rounded animate-pulse`
- Empty state: `py-12 text-center text-content-secondary`

### StatusBadge (`src/components/ui/StatusBadge.tsx`)

```
Classes base: inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium font-sans
Cor: definida por STATUS_CONFIG em src/lib/constants.ts
```

### AppSidebar (`src/components/layout/AppSidebar.tsx`)

- Container: `w-64 flex-col bg-white border-r border-border`
- Logo icon: `w-8 h-8 rounded-lg bg-primary` com SVG branco
- NavItem ativo: `bg-primary/10 text-primary`
- NavItem inativo: `text-content-secondary hover:bg-gray-100`
- Ícones Lucide: `w-4 h-4 strokeWidth={1.75}`
- Transições: `transition-colors`

---

## Estados de foco

```css
/* Global */
*:focus-visible {
  outline: 2px solid #1db954;
  outline-offset: 2px;
}

/* Inputs */
focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary
```

---

## Breakpoints

Tailwind defaults:

| Nome | px |
|---|---|
| `sm` | 640px |
| `md` | 768px |
| `lg` | 1024px |
| `xl` | 1280px |
| `2xl` | 1536px |

---

## CSS Variables (`:root`)

```css
--color-primary:        #1db954;
--color-primary-hover:  #1ed760;
--color-app-bg:         #f9fafb;
--color-surface:        #ffffff;
--color-text-primary:   #111827;
--color-text-secondary: #6b7280;
--color-border:         #e5e7eb;
```

---

## Utilitários (`src/lib/utils.ts`)

| Função | Descrição |
|---|---|
| `cn(...inputs)` | Merge de classes Tailwind (clsx + twMerge) |
| `parseDeadline(raw)` | Normaliza prazos conhecidos para dd/mm/aaaa |
| `truncate(text, max)` | Trunca com ellipsis |

---

## Arquivos-chave

| Arquivo | Conteúdo |
|---|---|
| `tailwind.config.ts` | Tokens customizados (cores, fontes, raios, sombras) |
| `src/app/globals.css` | CSS variables + `@layer base` + `@layer utilities` |
| `src/lib/constants.ts` | `SOURCE_COLORS`, `STATUS_CONFIG`, `SCORE_CONFIG` |
| `src/lib/utils.ts` | Funções utilitárias (`cn`, `parseDeadline`, `truncate`) |
| `src/components/ui/` | Componentes reutilizáveis |
| `src/components/layout/` | `AppSidebar`, `DashboardLayout` |
