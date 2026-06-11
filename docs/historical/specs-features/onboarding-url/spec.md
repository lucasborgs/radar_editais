# Feature: Onboarding por URL

## Contexto

O onboarding atual exige que o usuário preencha manualmente um wizard de 3 passos com até 19 campos.
A inspiração vem do Grantable (EUA): o usuário cola a URL do site da empresa e a LLM extrai o perfil automaticamente.

O wizard manual continua como fallback e como tela de revisão/edição.

---

## Requisitos

### Funcionais

**[URL-01]** O usuário pode colar uma URL no campo de entrada do onboarding.

**[URL-02]** O backend faz scraping da URL, extrai o HTML relevante e chama a LLM para inferir os campos do `CompanyProfile`.

**[URL-03]** Os campos extraídos são devolvidos ao frontend como um `CompanyProfile` parcial (campos não identificados ficam vazios).

**[URL-04]** O usuário vê o perfil pré-preenchido na tela de revisão (wizard em modo "review") e pode editar qualquer campo antes de confirmar.

**[URL-05]** Se a extração falhar (URL inválida, site inacessível, LLM não identificou campos suficientes), o sistema oferece dois caminhos: tentar outra URL ou preencher manualmente.

**[URL-06]** Campos mínimos obrigatórios para matching (`nome`, `tipo_entidade`, `one_liner`, `descricao_atividades`) são destacados caso a extração não os tenha encontrado.

**[URL-07]** O onboarding por URL substitui a tela inicial do wizard (step 0 atual), não os steps de revisão.

### Não-funcionais

**[URL-08]** Extração deve concluir em menos de 15s (timeout com feedback visual).

**[URL-09]** O endpoint não deve expor o conteúdo bruto do site — apenas o `CompanyProfile` extraído.

**[URL-10]** Funciona mesmo sem `OPENAI_API_KEY` configurada: nesse caso, pula extração e vai direto ao wizard manual.

---

## Fluxo principal

```
/onboarding
    └── [novo] Tela inicial: input de URL + botão "Extrair perfil"
            ↓ (POST /profile/extract)
        Loading state (≤15s)
            ↓
        Tela de revisão: wizard com campos pré-preenchidos
            ↓ (usuário edita e confirma)
        saveProfileToStorage() → redirect para ?next ou /matching
```

## Fluxo alternativo (falha)

```
POST /profile/extract → erro ou < 2 campos extraídos
    ↓
Banner: "Não conseguimos ler o site. Tente outra URL ou preencha manualmente."
    ↓
[Tentar outra URL] ou [Preencher manualmente → wizard passo 1 atual]
```

---

## Backend — novo endpoint

```
POST /profile/extract
Body: { url: string }
Response: {
  profile: CompanyProfile (parcial),
  confidence: { campo: "high" | "low" | "missing" },
  source_title: string   // título da página para feedback ao usuário
}
```

**Implementação:**
1. `LiveFetcher` (já existe) faz scraping da URL → retorna texto limpo
2. LLM recebe o texto + prompt estruturado → retorna JSON com campos do `CompanyProfile`
3. Validação e normalização antes de retornar

**Prompt de extração:**
- Instrução: "extraia do texto abaixo os campos da empresa"
- Schema de saída: JSON com os 19 campos do `CompanyProfile`
- Para cada campo: valor extraído ou `null` se não encontrado
- Temperatura: 0.1 (determinístico)

---

## Frontend — mudanças

### `app/onboarding/page.tsx`
- Novo estado inicial: `"url-input"` (antes de `step 0`)
- Input de URL + botão "Extrair perfil"
- Loading skeleton durante extração
- Em sucesso: inicializa wizard com `profile` pré-preenchido, abre no step 0 (revisão)
- Indicadores visuais por campo: ✓ extraído / ⚠ não encontrado (obrigatório) / — não encontrado (opcional)

### `lib/api.ts`
- Nova função: `extractProfileFromUrl(url: string): Promise<ExtractProfileResponse>`

### `types/profile.ts`
- Novo tipo: `FieldConfidence = "high" | "low" | "missing"`
- Novo tipo: `ExtractProfileResponse = { profile, confidence, source_title }`

---

## Critérios de aceite

- [ ] Colar URL de uma empresa real → wizard abre com nome, descrição e tipo pré-preenchidos
- [ ] Campos extraídos exibem indicador visual distinto de campos vazios
- [ ] URL inválida exibe mensagem de erro sem quebrar a página
- [ ] Timeout de 15s exibe estado de erro com opção de preencher manualmente
- [ ] Confirmar no wizard salva no localStorage e redireciona corretamente
- [ ] Sem `OPENAI_API_KEY`, vai direto ao wizard manual sem erro
