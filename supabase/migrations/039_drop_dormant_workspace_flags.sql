-- 039: remove preferências/flags de workspace sem consumidor atual.
--
-- `agent_explore_enabled` deixou de controlar o runtime: Explore usa sempre o
-- agente. `contribute_to_global_weights` era exposto como consentimento, mas
-- nenhum produtor ou matcher consumia a preferência. Antes desta migration, o
-- banco configurado tinha zero valores true em ambas as colunas.
--
-- Uma futura aprendizagem cross-workspace deve introduzir consentimento ligado
-- ao processamento real; não deve reutilizar preferência histórica silenciosa.

alter table public.workspaces
  drop column if exists agent_explore_enabled,
  drop column if exists contribute_to_global_weights;
