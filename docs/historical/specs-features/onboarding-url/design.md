# Design: Onboarding por URL

## Arquitetura

```
Frontend                    Backend                     Infra
─────────                   ───────                     ─────
OnboardingPage
  url-input state
       │ POST /profile/extract
       │ ─────────────────────────→  ProfileExtractor
       │                                  │ LiveFetcher.fetch_text(url)
       │                                  │   └─ GET url → BeautifulSoup → texto limpo
       │                                  │ LLM(texto) → CompanyProfile parcial
       │                                  │ normalizar + confidence map
       │ ←─────────────────────────       │
  review state
  (wizard pré-preenchido)
       │ saveProfileToStorage()
       ↓
  /matching
```

## Novo módulo: `core/profile_extractor.py`

Responsabilidade única: dado um texto de página web, retornar `CompanyProfile` parcial + confidence.

```python
class ProfileExtractor:
    def extract(self, url: str) -> ExtractResult:
        text = self._fetch_text(url)       # LiveFetcher adaptado
        profile, confidence = self._call_llm(text)
        return ExtractResult(profile, confidence, source_title)
```

### Reuso do LiveFetcher
`LiveFetcher` já faz scraping + extração de texto. Será estendido com `fetch_text(url) -> str`
que retorna o texto limpo (sem estruturar em seções) — mais adequado para extração de perfil.

### Prompt de extração
```
Você recebe o texto de um site corporativo brasileiro.
Extraia as seguintes informações da empresa no formato JSON.
Use null para campos não encontrados.

Schema:
{
  "nome": string | null,
  "tipo_entidade": "empresa"|"startup"|"universidade"|"ICT" | null,
  "one_liner": string | null,          // máx 1 frase
  "problem_statement": string | null,  // máx 2 frases
  "solution_summary": string | null,   // máx 2 frases
  "descricao_atividades": string | null,
  "tamanho_empresa": "MEI"|"ME"|"EPP"|"MEDIO"|"GRANDE" | null,
  "localizacao": string | null,
  "trl": int | null,
  "certificacoes": string[] | null
}

Texto do site:
{text[:4000]}
```

## Confidence map

Para cada campo: `"high"` se extraído com boa evidência textual, `"low"` se inferido, `"missing"` se null.
Implementação simples: `"missing"` se valor é null, `"high"` caso contrário (refinável futuramente).

## Tratamento de erros

| Situação | Comportamento |
|---|---|
| URL inválida / sem scheme | Validação no frontend antes de enviar |
| Timeout (>15s) | Backend retorna 408; frontend mostra opção fallback |
| Site inacessível | Backend retorna `{ profile: EMPTY, confidence: all-missing, error: "..." }` |
| LLM retorna JSON inválido | Try/except → retorna EMPTY_PROFILE sem erro 500 |
| < 2 campos extraídos | Backend retorna `low_confidence: true`; frontend sugere fallback |

## Indicadores visuais (frontend)

Cada campo no wizard em modo review recebe um ícone:
- `✓` verde — extraído com confiança
- `⚠` âmbar — campo obrigatório não encontrado
- (sem ícone) — campo opcional não encontrado

Implementação: `FieldConfidence` prop no componente `Field`, passada via context do wizard.
