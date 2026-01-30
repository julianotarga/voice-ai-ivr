# Voice AI IVR - FusionPBX Installer Specification

## ADDED Requirements

### Requirement: Detecção Automática de Ambiente

O instalador DEVE detectar automaticamente o ambiente FusionPBX existente no servidor, incluindo caminhos, credenciais e configurações, sem requerer input manual para configurações padrão.

#### Scenario: Detecção de FusionPBX em caminho padrão
- **GIVEN** servidor com FusionPBX instalado em `/var/www/fusionpbx`
- **WHEN** o instalador é executado
- **THEN** o instalador detecta o caminho automaticamente
- **AND** extrai credenciais do banco de `/var/www/fusionpbx/resources/config.php`
- **AND** exibe as configurações detectadas para confirmação

#### Scenario: Detecção de FusionPBX em caminho customizado
- **GIVEN** servidor com FusionPBX instalado em `/opt/fusionpbx`
- **WHEN** o instalador é executado com `--fusionpbx-path=/opt/fusionpbx`
- **THEN** o instalador usa o caminho especificado
- **AND** extrai credenciais corretamente

#### Scenario: FusionPBX não encontrado
- **GIVEN** servidor sem FusionPBX instalado
- **WHEN** o instalador é executado
- **THEN** o instalador exibe erro claro
- **AND** sugere verificar a instalação do FusionPBX
- **AND** encerra com código de saída 1

---

### Requirement: Validação de Pré-requisitos

O instalador DEVE validar todos os pré-requisitos necessários antes de iniciar qualquer modificação no sistema.

#### Scenario: Todos os pré-requisitos atendidos
- **GIVEN** servidor com FusionPBX, FreeSWITCH rodando, PostgreSQL acessível
- **AND** Python 3.11+ instalado
- **AND** espaço em disco suficiente (>500MB)
- **WHEN** o instalador valida pré-requisitos
- **THEN** exibe checklist verde para todos os itens
- **AND** continua para próxima fase

#### Scenario: FreeSWITCH não está rodando
- **GIVEN** servidor com FusionPBX mas FreeSWITCH parado
- **WHEN** o instalador valida pré-requisitos
- **THEN** exibe erro indicando que FreeSWITCH deve estar rodando
- **AND** sugere comando para iniciar o serviço
- **AND** encerra com código de saída 1

#### Scenario: Python 3.11 não disponível
- **GIVEN** servidor sem Python 3.11
- **WHEN** o instalador valida pré-requisitos
- **THEN** oferece opção de instalar Python 3.11 automaticamente
- **AND** aguarda confirmação do usuário

#### Scenario: Espaço em disco insuficiente
- **GIVEN** servidor com menos de 500MB livres
- **WHEN** o instalador valida pré-requisitos
- **THEN** exibe aviso de espaço insuficiente
- **AND** mostra espaço disponível e espaço necessário
- **AND** encerra com código de saída 1

---

### Requirement: Instalação Idempotente do Banco de Dados

O instalador DEVE executar todas as migrations de forma idempotente, permitindo execução múltipla sem causar erros ou duplicação de dados.

#### Scenario: Primeira instalação
- **GIVEN** banco de dados FusionPBX sem tabelas do Voice AI
- **WHEN** o instalador executa migrations
- **THEN** todas as tabelas `v_voice_*` são criadas
- **AND** índices e constraints são criados
- **AND** providers padrão são inseridos
- **AND** log mostra "X tabelas criadas"

#### Scenario: Reinstalação com tabelas existentes
- **GIVEN** banco de dados com tabelas Voice AI já existentes
- **WHEN** o instalador executa migrations novamente
- **THEN** nenhum erro é gerado
- **AND** tabelas existentes são preservadas
- **AND** colunas novas são adicionadas se necessário
- **AND** log mostra "Tabelas já existem, verificando atualizações"

#### Scenario: Falha durante migration
- **GIVEN** erro de conexão durante execução de migration
- **WHEN** o instalador detecta a falha
- **THEN** executa rollback do banco para estado anterior
- **AND** log mostra operações revertidas
- **AND** encerra com código de saída 1

---

### Requirement: Instalação do App FusionPBX

O instalador DEVE instalar a aplicação PHP `voice_secretary` no diretório de apps do FusionPBX e registrá-la corretamente no sistema.

#### Scenario: Instalação do app em sistema limpo
- **GIVEN** FusionPBX sem app voice_secretary instalado
- **WHEN** o instalador executa instalação do app
- **THEN** diretório `voice_secretary` é copiado para `/var/www/fusionpbx/app/`
- **AND** permissões são ajustadas para www-data:www-data
- **AND** `upgrade.php` é executado para registrar app
- **AND** menu "Secretária Virtual" aparece no FusionPBX
- **AND** permissões `voice_secretary_*` são criadas

#### Scenario: Atualização de app existente
- **GIVEN** FusionPBX com versão anterior do voice_secretary
- **WHEN** o instalador executa com `--upgrade`
- **THEN** backup do app anterior é criado
- **AND** novo código é copiado
- **AND** configurações no banco são preservadas
- **AND** cache do FusionPBX é limpo

#### Scenario: Conflito de permissões
- **GIVEN** diretório `/var/www/fusionpbx/app/` sem permissão de escrita
- **WHEN** o instalador tenta copiar app
- **THEN** exibe erro claro sobre permissões
- **AND** sugere comando para corrigir
- **AND** não corrompe estado do sistema

---

### Requirement: Instalação do Serviço Python

O instalador DEVE instalar o serviço Python Voice AI como daemon systemd com isolamento adequado.

#### Scenario: Instalação completa do serviço
- **GIVEN** servidor com Python 3.11 disponível
- **WHEN** o instalador executa instalação do serviço
- **THEN** usuário `voiceai` é criado se não existir
- **AND** diretório `/opt/voice-ai` é criado com estrutura correta
- **AND** virtual environment é criado com Python 3.11
- **AND** dependências são instaladas do requirements.txt
- **AND** arquivo `.env` é gerado com configurações detectadas
- **AND** units systemd são instaladas
- **AND** logrotate é configurado
- **AND** serviço é habilitado e iniciado

#### Scenario: Serviço inicia corretamente
- **GIVEN** instalação do serviço concluída
- **WHEN** o instalador verifica status
- **THEN** serviço `voice-ai-realtime` está rodando
- **AND** porta 8022 está escutando
- **AND** porta 8085 está escutando
- **AND** conexão ESL com FreeSWITCH está ativa

#### Scenario: Falha ao instalar dependências
- **GIVEN** dependência não disponível no PyPI
- **WHEN** pip install falha
- **THEN** instalador exibe erro com nome da dependência
- **AND** sugere verificar conexão de rede
- **AND** executa rollback do serviço

---

### Requirement: Configuração de Dialplan

O instalador DEVE criar dialplan para roteamento de chamadas ao Voice AI inserindo registros diretamente no banco de dados do FusionPBX (tabelas `v_dialplans` e `v_dialplan_details`).

#### Scenario: Criação de dialplan via banco de dados
- **GIVEN** FusionPBX funcional com dialplan manager
- **WHEN** o instalador configura dialplan
- **THEN** registro é inserido em `v_dialplans` com app_uuid do Voice AI
- **AND** detalhes são inseridos em `v_dialplan_details` (condition e action socket)
- **AND** `fs_cli -x "reloadxml"` é executado
- **AND** dialplan aparece na UI do FusionPBX (Dialplan > Dialplan Manager)

#### Scenario: Dialplan gerenciável via UI
- **GIVEN** dialplan inserido no banco
- **WHEN** administrador acessa Dialplan Manager no FusionPBX
- **THEN** dialplan "Voice AI Secretary" é listado
- **AND** pode ser editado/desabilitado pela interface web

#### Scenario: Dialplan funciona corretamente
- **GIVEN** dialplan instalado
- **AND** secretária virtual configurada com extensão 8000
- **WHEN** chamada é feita para extensão 8000
- **THEN** chamada é roteada para Voice AI via `socket 127.0.0.1:8022 async full`
- **AND** ESL Outbound é conectado
- **AND** WebSocket com OpenAI é estabelecido

---

### Requirement: Sistema de Backup e Rollback

O instalador DEVE criar backups antes de modificações críticas e permitir rollback automático em caso de falha.

#### Scenario: Backup automático antes de migrations
- **GIVEN** instalação em andamento
- **WHEN** fase de migrations inicia
- **THEN** backup do banco é criado em `/tmp/voice-ai-install-{timestamp}/`
- **AND** log registra caminho do backup

#### Scenario: Rollback após falha
- **GIVEN** falha durante instalação do serviço
- **WHEN** instalador detecta erro
- **THEN** banco é restaurado do backup
- **AND** arquivos copiados são removidos
- **AND** estado anterior é restaurado
- **AND** log detalha todas as reversões

#### Scenario: Limpeza após sucesso
- **GIVEN** instalação concluída com sucesso
- **WHEN** instalador finaliza
- **THEN** arquivos temporários são removidos
- **AND** backups são mantidos por 7 dias (configurável)

---

### Requirement: Modo Dry-Run

O instalador DEVE suportar modo dry-run que mostra todas as alterações que seriam feitas sem executá-las.

#### Scenario: Execução em modo dry-run
- **GIVEN** servidor com FusionPBX
- **WHEN** instalador é executado com `--dry-run`
- **THEN** todas as verificações são executadas
- **AND** lista de operações é exibida
- **AND** nenhuma alteração é feita no sistema
- **AND** log mostra "[DRY-RUN]" em cada operação

---

### Requirement: Modo Uninstall

O instalador DEVE suportar remoção completa do Voice AI preservando o FusionPBX intacto.

#### Scenario: Desinstalação completa
- **GIVEN** Voice AI instalado no servidor
- **WHEN** instalador é executado com `--uninstall`
- **THEN** confirmação é solicitada ao usuário
- **AND** serviços são parados e removidos
- **AND** diretório `/opt/voice-ai` é removido
- **AND** app é removido de `/var/www/fusionpbx/app/`
- **AND** dialplan é removido do banco
- **AND** opcionalmente tabelas são removidas (com confirmação extra)

#### Scenario: Desinstalação preserva banco
- **GIVEN** Voice AI instalado com dados de conversas
- **WHEN** instalador é executado com `--uninstall --keep-data`
- **THEN** tabelas do banco são preservadas
- **AND** apenas arquivos e serviços são removidos

---

### Requirement: Ferramentas de Diagnóstico

O instalador DEVE incluir ferramentas para verificação de saúde e troubleshooting do sistema instalado.

#### Scenario: Verificação de saúde completa
- **GIVEN** Voice AI instalado
- **WHEN** `voice-ai-status` é executado
- **THEN** status de cada componente é exibido
- **AND** serviços systemd são verificados
- **AND** portas são verificadas
- **AND** conexão ESL é testada
- **AND** conexão com banco é testada

#### Scenario: Visualização de logs
- **GIVEN** Voice AI em execução
- **WHEN** `voice-ai-logs` é executado
- **THEN** logs são exibidos em tempo real
- **AND** filtro por nível de log é suportado
- **AND** Ctrl+C encerra visualização

---

### Requirement: Compatibilidade Multi-Distro

O instalador DEVE funcionar em múltiplas distribuições Linux suportadas.

#### Scenario: Instalação em Ubuntu 22.04
- **GIVEN** servidor Ubuntu 22.04 com FusionPBX
- **WHEN** instalador é executado
- **THEN** instalação é concluída com sucesso
- **AND** todos os componentes funcionam

#### Scenario: Instalação em Ubuntu 24.04
- **GIVEN** servidor Ubuntu 24.04 com FusionPBX
- **WHEN** instalador é executado
- **THEN** instalação é concluída com sucesso

#### Scenario: Instalação em Debian 12
- **GIVEN** servidor Debian 12 com FusionPBX
- **WHEN** instalador é executado
- **THEN** instalação é concluída com sucesso

#### Scenario: Sistema operacional não suportado
- **GIVEN** servidor com CentOS 7
- **WHEN** instalador é executado
- **THEN** aviso é exibido sobre sistema não testado
- **AND** usuário pode continuar por conta própria com `--force`

---

### Requirement: Logs e Auditoria

O instalador DEVE gerar logs detalhados de todas as operações para auditoria e troubleshooting.

#### Scenario: Log de instalação completo
- **GIVEN** instalação em andamento
- **WHEN** cada operação é executada
- **THEN** log é gravado em `/var/log/voice-ai/install.log`
- **AND** timestamp é incluído em cada linha
- **AND** nível de severidade é indicado (INFO, WARN, ERROR)
- **AND** comandos executados são registrados
- **AND** senhas são mascaradas como `***REDACTED***`

#### Scenario: Resumo final de instalação
- **GIVEN** instalação concluída
- **WHEN** instalador finaliza
- **THEN** resumo é exibido com status de cada fase
- **AND** tempo total de instalação é mostrado
- **AND** próximos passos são listados
- **AND** URLs de acesso são exibidas
