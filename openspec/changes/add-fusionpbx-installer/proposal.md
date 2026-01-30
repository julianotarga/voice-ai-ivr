# Change: Instalador Automatizado para FusionPBX

## Why

Atualmente a instalação do Voice AI IVR em servidores FusionPBX é um processo manual, propenso a erros e que requer conhecimento técnico aprofundado. Os administradores precisam executar múltiplos passos separados: criar tabelas no banco de dados, copiar arquivos da aplicação PHP, configurar permissões, criar dialplans, configurar systemd, e muito mais. 

Um instalador automatizado e minucioso é essencial para:
- **Reduzir tempo de implantação** de horas para minutos
- **Eliminar erros humanos** na configuração
- **Garantir consistência** entre diferentes instalações
- **Facilitar atualizações** futuras do sistema
- **Permitir rollback** em caso de problemas

## What Changes

### Novo Script de Instalação
- Script principal `install-fusionpbx.sh` com interface interativa
- Detecção automática do ambiente FusionPBX existente
- Verificações de pré-requisitos antes de qualquer modificação
- Modo dry-run para preview das mudanças
- Rollback automático em caso de falha

### Instalação do Banco de Dados
- Detecção automática das credenciais PostgreSQL do FusionPBX
- Execução idempotente de todas as migrations
- Backup automático do banco antes de alterações
- Verificação de integridade pós-instalação

### Instalação da Aplicação FusionPBX
- Cópia do app `voice_secretary` para `/var/www/fusionpbx/app/`
- Preservação de permissões e ownership
- Registro da aplicação no FusionPBX via `app_config.php`
- Atualização de menus e permissões via PHP CLI

### Instalação do Serviço Voice AI
- Criação de ambiente virtual Python isolado
- Instalação de dependências do requirements.txt
- Configuração de systemd units
- Configuração de logrotate

### Configuração de Integração
- Criação automática de dialplan via banco de dados (`v_dialplans`, `v_dialplan_details`)
- Dialplan gerenciável pela UI do FusionPBX (Dialplan Manager)
- Configuração de ESL (event_socket)
- Geração de arquivo `.env` com valores detectados
- Configuração de firewall (ufw) se presente

### Ferramentas de Diagnóstico
- Script `voice-ai-status.sh` para verificação de saúde
- Script `voice-ai-logs.sh` para visualização de logs
- Script `voice-ai-uninstall.sh` para remoção limpa

## Impact

- **Affected specs**: Nenhum spec existente será modificado
- **Affected code**: 
  - `deploy/` - Nova estrutura de instalação
  - `database/migrations/` - Consolidação de migrations
  - `fusionpbx-app/` - Sem alterações
- **Affected systems**:
  - Servidores FusionPBX target (alterações de banco e filesystem)
- **Risk**: **BAIXO** - Instalador é aditivo, não modifica código existente do FusionPBX

## Success Criteria

1. Instalação completa em menos de 5 minutos
2. Zero intervenção manual após início do script
3. Rollback funcional que restaura estado anterior
4. Compatibilidade com Ubuntu 22.04, Ubuntu 24.04, Debian 12
5. Compatibilidade com FusionPBX 5.x
6. Logs detalhados de todas as operações
7. Verificação de saúde pós-instalação com sucesso
