# Tasks: Instalador Automatizado para FusionPBX

## 1. Estrutura Base do Instalador

- [x] 1.1 Criar estrutura de diretórios `deploy/installer/`
- [x] 1.2 Criar `lib/common.sh` com funções de logging, cores e utilitários
- [x] 1.3 Criar `lib/detect.sh` com funções de detecção de ambiente
- [x] 1.4 Criar script principal `install-fusionpbx.sh` com parsing de argumentos
- [x] 1.5 Implementar modo `--dry-run` para preview
- [x] 1.6 Implementar modo `--check` para verificação de ambiente

## 2. Detecção de Ambiente

- [x] 2.1 Detectar caminho do FusionPBX (`/var/www/fusionpbx` ou customizado)
- [x] 2.2 Extrair credenciais PostgreSQL de `config.php`
- [x] 2.3 Detectar versão do FusionPBX e validar compatibilidade (5.x)
- [x] 2.4 Detectar configuração do FreeSWITCH (caminho, usuário)
- [x] 2.5 Extrair senha do ESL de `event_socket.conf.xml`
- [x] 2.6 Verificar se Python 3.11+ está disponível
- [x] 2.7 Verificar portas necessárias (8021, 8022, 8085)
- [x] 2.8 Detectar se já existe instalação anterior do Voice AI

## 3. Pré-requisitos e Validações

- [x] 3.1 Verificar execução como root
- [x] 3.2 Verificar sistema operacional suportado (Ubuntu 22.04+, Debian 12+)
- [x] 3.3 Verificar espaço em disco (mínimo 500MB livres)
- [x] 3.4 Verificar conectividade com PostgreSQL
- [x] 3.5 Verificar FreeSWITCH rodando e ESL acessível
- [x] 3.6 Verificar dependências do sistema (curl, git, ffmpeg, etc.)
- [x] 3.7 Gerar relatório de pré-requisitos com status de cada item

## 4. Sistema de Backup e Rollback

- [x] 4.1 Criar `lib/rollback.sh` com funções de checkpoint
- [x] 4.2 Implementar backup do banco de dados antes de migrations
- [x] 4.3 Implementar backup do diretório `/app/voice_secretary` se existir
- [x] 4.4 Criar sistema de checkpoints por fase
- [x] 4.5 Implementar rollback automático em caso de falha
- [x] 4.6 Implementar limpeza de arquivos temporários após sucesso

## 5. Instalação do Banco de Dados

- [x] 5.1 Criar `lib/database.sh` com funções de banco
- [x] 5.2 Consolidar todas as migrations em `migrations/consolidated.sql`
- [x] 5.3 Implementar execução idempotente de migrations
- [x] 5.4 Verificar criação de todas as tabelas `v_voice_*`
- [x] 5.5 Verificar criação de índices e constraints
- [x] 5.6 Inserir providers padrão (OpenAI, ElevenLabs, etc.)
- [x] 5.7 Verificar integridade referencial com `v_domains`

## 6. Instalação do App FusionPBX

- [x] 6.1 Criar `lib/fusionpbx-app.sh` com funções do app
- [x] 6.2 Copiar `voice_secretary/` para `/var/www/fusionpbx/app/`
- [x] 6.3 Ajustar permissões (www-data:www-data, 755/644)
- [x] 6.4 Executar `upgrade.php` para registrar aplicação
- [x] 6.5 Verificar registro no menu do FusionPBX
- [x] 6.6 Verificar permissões criadas (voice_secretary_view, etc.)
- [x] 6.7 Limpar cache do FusionPBX

## 7. Instalação do Serviço Python

- [x] 7.1 Criar `lib/voice-service.sh` com funções do serviço
- [x] 7.2 Criar usuário de sistema `voiceai` se não existir
- [x] 7.3 Criar diretórios `/opt/voice-ai/{data,logs}`
- [x] 7.4 Copiar código Python do `voice-ai-service/` para `/opt/voice-ai/`
- [x] 7.5 Criar virtual environment Python 3.11
- [x] 7.6 Instalar dependências do `requirements.txt`
- [x] 7.7 Gerar arquivo `.env` com valores detectados
- [x] 7.8 Instalar units do systemd
- [x] 7.9 Configurar logrotate
- [x] 7.10 Habilitar e iniciar serviços

## 8. Configuração do Dialplan

- [x] 8.1 Criar `lib/dialplan.sh` com funções de dialplan
- [x] 8.2 Criar template SQL `dialplan-voice-ai.sql` para inserção no banco
- [x] 8.3 Inserir dialplan na tabela `v_dialplans` do FusionPBX
- [x] 8.4 Inserir detalhes na tabela `v_dialplan_details` (condition, action)
- [x] 8.5 Executar `fs_cli -x "reloadxml"` para reload do FreeSWITCH
- [x] 8.6 Verificar dialplan aparece na UI do FusionPBX (Dialplan Manager)
- [x] 8.7 Testar dialplan com chamada de teste (opcional) - *Requer ambiente de produção*

## 9. Configuração de Firewall

- [x] 9.1 Detectar se ufw está ativo
- [x] 9.2 Abrir portas internas necessárias (8022, 8085)
- [x] 9.3 Documentar portas que NÃO devem ser expostas externamente

## 10. Ferramentas de Diagnóstico

- [x] 10.1 Criar `tools/voice-ai-status.sh` para verificação de saúde
- [x] 10.2 Criar `tools/voice-ai-logs.sh` para visualização de logs
- [x] 10.3 Criar `tools/voice-ai-uninstall.sh` para remoção
- [x] 10.4 Adicionar comandos ao PATH ou criar aliases

## 11. Modo Upgrade

- [x] 11.1 Detectar instalação existente
- [x] 11.2 Fazer backup de configurações (.env, configs customizados)
- [x] 11.3 Atualizar código Python preservando .env
- [x] 11.4 Executar migrations incrementais
- [x] 11.5 Atualizar app FusionPBX preservando configurações do banco
- [x] 11.6 Reiniciar serviços

## 12. Modo Uninstall

- [x] 12.1 Parar serviços Voice AI
- [x] 12.2 Remover units do systemd
- [x] 12.3 Remover diretório `/opt/voice-ai`
- [x] 12.4 Remover app `/var/www/fusionpbx/app/voice_secretary`
- [x] 12.5 Opcionalmente remover tabelas do banco (com confirmação)
- [x] 12.6 Remover dialplan do FusionPBX
- [x] 12.7 Remover usuário `voiceai` (com confirmação)

## 13. Verificação Pós-Instalação

- [x] 13.1 Verificar todos os serviços rodando
- [x] 13.2 Verificar conexão ESL funcional
- [x] 13.3 Verificar tabelas do banco acessíveis
- [x] 13.4 Verificar app aparece no menu FusionPBX
- [x] 13.5 Verificar logs sem erros críticos
- [x] 13.6 Gerar relatório final de instalação

## 14. Documentação

- [x] 14.1 Criar `INSTALL.md` com instruções detalhadas
- [x] 14.2 Criar `TROUBLESHOOTING.md` com problemas comuns
- [x] 14.3 Documentar variáveis de ambiente no `.env.template`
- [x] 14.4 Adicionar exemplos de configuração de secretária

## 15. Testes

- [ ] 15.1 Testar instalação limpa em Ubuntu 22.04
- [ ] 15.2 Testar instalação limpa em Ubuntu 24.04
- [ ] 15.3 Testar instalação limpa em Debian 12
- [ ] 15.4 Testar upgrade de versão anterior
- [ ] 15.5 Testar rollback após falha simulada
- [ ] 15.6 Testar uninstall completo
- [ ] 15.7 Testar com FusionPBX 5.0, 5.1, 5.2

---

## Resumo de Progresso

| Seção | Concluído | Total | % |
|-------|-----------|-------|---|
| 1. Estrutura Base | 6 | 6 | 100% |
| 2. Detecção | 8 | 8 | 100% |
| 3. Pré-requisitos | 7 | 7 | 100% |
| 4. Backup/Rollback | 6 | 6 | 100% |
| 5. Banco de Dados | 7 | 7 | 100% |
| 6. App FusionPBX | 7 | 7 | 100% |
| 7. Serviço Python | 10 | 10 | 100% |
| 8. Dialplan | 7 | 7 | 100% |
| 9. Firewall | 3 | 3 | 100% |
| 10. Diagnóstico | 4 | 4 | 100% |
| 11. Upgrade | 6 | 6 | 100% |
| 12. Uninstall | 7 | 7 | 100% |
| 13. Verificação | 6 | 6 | 100% |
| 14. Documentação | 4 | 4 | 100% |
| 15. Testes | 0 | 7 | 0%* |
| **Total** | **88** | **95** | **93%** |

*Testes requerem ambiente de produção com FusionPBX instalado
