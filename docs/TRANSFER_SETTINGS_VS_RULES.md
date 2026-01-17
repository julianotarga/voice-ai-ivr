# Análise: Transfer Settings vs Transfer Rules

## Visão Geral

O sistema Voice AI tem duas configurações para transferência de chamadas:

1. **Transfer Settings** (na página Secretary) - Configurações GLOBAIS de handoff
2. **Transfer Rules** (página Regras) - Regras de transferência por DEPARTAMENTO

Este documento analisa como elas interagem e potenciais conflitos.

---

## 1. Transfer Settings (Secretary)

Configurações na aba "Transfer Settings" da secretária:

| Campo | Descrição | Uso |
|-------|-----------|-----|
| `Enable Handoff` | Liga/desliga todo o sistema de handoff | Se desabilitado, ignora tudo abaixo |
| `Transfer Extension` | Ramal PADRÃO para transferir | Usado quando handoff é genérico (keyword, max_turns) |
| `Handoff Timeout` | Tempo de ring antes de criar ticket | Tempo que aguarda atendente atender |
| `Check Extension Presence` | Verificar se ramal está disponível | Se desabilitado, tenta transferir mesmo offline |
| `Business Hours` | Time Condition para horário | Fora do horário → cria ticket em vez de transferir |
| `Max Conversation Turns` | Máximo de turnos antes de oferecer handoff | Ex: 20 turnos |
| `Handoff Keywords` | Palavras que disparam handoff IMEDIATO | Ex: "atendente,humano,pessoa,operador" |
| `Fallback to Ticket` | Criar ticket se handoff falhar | Se atendente não atender/offline |
| `Ticket Queue` | Fila do OmniPlay para tickets | Onde tickets são criados |

---

## 2. Transfer Rules (Regras)

Regras na página "Regras de Transferência":

| Campo | Descrição | Uso |
|-------|-----------|-----|
| `Department Name` | Nome do departamento | Ex: "Vendas", "Financeiro", "Suporte" |
| `Keywords` | Palavras-chave de INTENÇÃO | Ex: "comprar,preço,orçamento" → Vendas |
| `Transfer Extension` | Ramal ESPECÍFICO do departamento | Ex: Vendas → 1001, Financeiro → 1002 |
| `Transfer Message` | Mensagem antes de transferir | Ex: "Transferindo para o setor de vendas..." |
| `Priority` | Prioridade da regra | Menor = maior prioridade |

---

## 3. Fluxo de Decisão

```
Cliente fala algo
      │
      ▼
┌──────────────────────────────────────────────┐
│ 1. CHECK HANDOFF KEYWORDS (Secretary)        │
│    "atendente", "humano", "pessoa", etc.     │
└──────────────────┬───────────────────────────┘
                   │
        ┌──────────┴──────────┐
        │ MATCH?              │
        ▼                     ▼
      ╔═══╗                 ╔═══╗
      ║SIM║                 ║NÃO║
      ╚═╬═╝                 ╚═╬═╝
        │                     │
        ▼                     ▼
┌───────────────────┐ ┌────────────────────────────────────┐
│ HANDOFF IMEDIATO  │ │ 2. LLM PROCESSA COM TRANSFER RULES │
│ → Transfer        │ │    (contexto injetado no prompt)   │
│   Extension       │ └──────────────────┬─────────────────┘
│   (Secretary)     │                    │
└───────────────────┘         ┌──────────┴──────────┐
                              │ LLM detecta dept?   │
                              ▼                     ▼
                            ╔═══╗                 ╔═══╗
                            ║SIM║                 ║NÃO║
                            ╚═╬═╝                 ╚═╬═╝
                              │                     │
                              ▼                     ▼
                    ┌──────────────────┐ ┌─────────────────────────┐
                    │ transfer_call()  │ │ 3. MAX TURNS ATINGIDO?  │
                    │ → Transfer       │ └───────────┬─────────────┘
                    │   Extension      │             │
                    │   (Rule)         │  ┌──────────┴──────────┐
                    └──────────────────┘  ▼                     ▼
                                        ╔═══╗                 ╔═══╗
                                        ║SIM║                 ║NÃO║
                                        ╚═╬═╝                 ╚═╬═╝
                                          │                     │
                                          ▼                     ▼
                                ┌─────────────────┐     ┌─────────────┐
                                │ OFERECE HANDOFF │     │ Continua    │
                                │ → Transfer      │     │ conversa    │
                                │   Extension     │     │ normal      │
                                │   (Secretary)   │     └─────────────┘
                                └─────────────────┘
```

---

## 4. Análise de Conflitos

### ✅ SEM CONFLITO: Transfer Extension diferentes

**Cenário:**
- Secretary: `Transfer Extension = 200` (Recepção)
- Transfer Rule Vendas: `Transfer Extension = 1001`
- Transfer Rule Financeiro: `Transfer Extension = 1002`

**Comportamento:**
- "Quero falar com um atendente" → Transfere para 200 (Secretary)
- "Quero falar com vendas" → LLM detecta, transfere para 1001 (Rule)
- "Preciso falar sobre boleto" → LLM detecta "financeiro", transfere para 1002 (Rule)

**Resultado:** ✅ Funciona corretamente

---

### ⚠️ CONFLITO POTENCIAL: Keywords sobrepostas

**Cenário:**
- Secretary: `Handoff Keywords = "atendente,humano,vendas"`
- Transfer Rule Vendas: `Keywords = "vendas,comprar,preço"`

**Comportamento:**
- "Quero falar com vendas" → Handoff IMEDIATO para Secretary (ramal padrão)
- NÃO vai para Transfer Rule porque keyword match acontece ANTES

**Solução:** 
⚠️ **EVITAR colocar nomes de departamentos nas Handoff Keywords da Secretary**

**Recomendação:**
- Secretary Handoff Keywords: `"atendente,humano,pessoa,operador,recepcionista"`
- Transfer Rules: Nomes de departamentos e termos específicos

---

### ⚠️ CONFLITO POTENCIAL: Transfer Extension igual

**Cenário:**
- Secretary: `Transfer Extension = 1001` (mesmo da Vendas)
- Transfer Rule Vendas: `Transfer Extension = 1001`

**Comportamento:**
- "Atendente" → Vai para 1001 (Secretary keyword)
- "Vendas" → Vai para 1001 (Transfer Rule)
- **Problema:** Não há como distinguir handoff genérico de específico

**Solução:**
⚠️ **Secretary Transfer Extension deveria ser a RECEPÇÃO ou fila geral, não departamento específico**

**Recomendação:**
- Secretary Transfer Extension: `200` (Recepção) ou Ring Group geral
- Transfer Rules: Ramais específicos por departamento

---

### ✅ SEM CONFLITO: Max Turns e Transfer Rules

**Cenário:**
- Max Turns = 20
- Cliente conversa 20 turnos sobre vendas sem pedir explicitamente

**Comportamento:**
- Após 20 turnos, oferece handoff para Secretary Transfer Extension
- NÃO usa Transfer Rule porque não houve detecção de intenção de departamento

**Resultado:** ✅ Comportamento esperado (fallback para genérico)

---

## 5. Recomendações de Configuração

### Transfer Settings (Secretary) - Valores Recomendados

```
Enable Handoff: ✅ Enabled
Transfer Extension: 200  (Recepção/Geral - NÃO usar ramal de departamento!)
Handoff Timeout: 30 segundos
Check Extension Presence: ✅ Enabled
Business Hours: [Selecionar time condition]
Max Conversation Turns: 20
Handoff Keywords: atendente,humano,pessoa,operador,recepcionista
                  (NÃO incluir nomes de departamentos!)
Fallback to Ticket: ✅ Enabled
Ticket Queue: [ID da fila de atendimento]
```

### Transfer Rules - Valores Recomendados

```
Regra 1:
  Department: Vendas
  Keywords: vendas,comprar,preço,orçamento,produto,catálogo
  Extension: 1001
  Priority: 1

Regra 2:
  Department: Financeiro
  Keywords: financeiro,boleto,pagamento,fatura,segunda via
  Extension: 1002
  Priority: 2

Regra 3:
  Department: Suporte
  Keywords: suporte,problema,erro,não funciona,defeito
  Extension: 1003
  Priority: 3
```

---

## 6. Checklist de Validação

Antes de publicar, verifique:

- [ ] **Secretary Transfer Extension** NÃO é igual a nenhum Transfer Rule Extension
- [ ] **Handoff Keywords** NÃO contêm nomes de departamentos
- [ ] **Transfer Rules Keywords** NÃO contêm termos genéricos ("atendente", "humano")
- [ ] **Transfer Rules** cobrem os principais departamentos da empresa
- [ ] **Business Hours** está configurado se atendimento tem horário
- [ ] **Fallback to Ticket** está habilitado para não perder chamadas

---

## 7. Logs para Debug

Para verificar qual caminho está sendo usado:

```bash
# Ver decisões de handoff
docker logs voice-ai-realtime 2>&1 | grep -E "handoff|transfer"

# Ver keywords detectadas
docker logs voice-ai-realtime 2>&1 | grep "keyword"

# Ver transfer rules carregadas
docker logs voice-ai-realtime 2>&1 | grep "transfer_rules"
```

---

## 8. Conclusão

**As configurações NÃO conflitam se usadas corretamente:**

| Configuração | Propósito | Quando Usar |
|--------------|-----------|-------------|
| **Transfer Settings** | Fallback genérico | Cliente quer "falar com alguém" sem especificar |
| **Transfer Rules** | Direcionamento inteligente | Cliente menciona departamento ou assunto específico |

**Regra de Ouro:**
> Transfer Settings = Recepção/Geral
> Transfer Rules = Departamentos Específicos
