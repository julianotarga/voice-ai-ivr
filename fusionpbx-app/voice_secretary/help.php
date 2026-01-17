<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - Help & Tutorial
	Guia completo de configuração da integração Voice AI IVR + OmniPlay
*/

//includes files
	require_once dirname(__DIR__, 2) . "/resources/require.php";

//check permissions
	if (permission_exists('voice_secretary_view')) {
		//access granted
	}
	else {
		echo "access denied";
		exit;
	}

//add multi-lingual support
	$language = new text;
	$text = $language->get();

//set current page for nav
	$current_page = 'help';

//include header
	require_once "resources/header.php";

//include navigation
	require_once "resources/nav_tabs.php";

//page title
	echo "<b class='heading'>".($text['title-help'] ?? '❓ Guia de Configuração - Voice AI IVR')."</b><br><br>";

?>

<style>
/* Help Page Styles */
.help-container {
	max-width: 1200px;
	font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

.help-section {
	background: #fff;
	border: 1px solid #e0e0e0;
	border-radius: 8px;
	margin-bottom: 25px;
	overflow: hidden;
}

.help-section-header {
	background: linear-gradient(135deg, #1e88e5, #1565c0);
	color: #fff;
	padding: 15px 20px;
	font-size: 18px;
	font-weight: 600;
	cursor: pointer;
	display: flex;
	justify-content: space-between;
	align-items: center;
}

.help-section-header:hover {
	background: linear-gradient(135deg, #1976d2, #0d47a1);
}

.help-section-header .toggle-icon {
	font-size: 20px;
	transition: transform 0.3s;
}

.help-section-header.collapsed .toggle-icon {
	transform: rotate(-90deg);
}

.help-section-content {
	padding: 20px;
	line-height: 1.7;
}

.help-section-content.hidden {
	display: none;
}

.step-box {
	background: #f8f9fa;
	border-left: 4px solid #1e88e5;
	padding: 15px 20px;
	margin: 15px 0;
	border-radius: 0 8px 8px 0;
}

.step-box h4 {
	margin: 0 0 10px 0;
	color: #1e88e5;
}

.step-number {
	display: inline-block;
	background: #1e88e5;
	color: #fff;
	width: 28px;
	height: 28px;
	border-radius: 50%;
	text-align: center;
	line-height: 28px;
	font-weight: bold;
	margin-right: 10px;
}

.code-block {
	background: #263238;
	color: #aed581;
	padding: 15px;
	border-radius: 6px;
	font-family: 'Monaco', 'Consolas', monospace;
	font-size: 13px;
	overflow-x: auto;
	margin: 10px 0;
}

.code-block .comment {
	color: #78909c;
}

.warning-box {
	background: #fff3e0;
	border: 1px solid #ffb74d;
	border-left: 4px solid #ff9800;
	padding: 15px;
	border-radius: 0 8px 8px 0;
	margin: 15px 0;
}

.warning-box h4 {
	color: #e65100;
	margin: 0 0 10px 0;
}

.success-box {
	background: #e8f5e9;
	border: 1px solid #81c784;
	border-left: 4px solid #4caf50;
	padding: 15px;
	border-radius: 0 8px 8px 0;
	margin: 15px 0;
}

.success-box h4 {
	color: #2e7d32;
	margin: 0 0 10px 0;
}

.info-box {
	background: #e3f2fd;
	border: 1px solid #90caf9;
	border-left: 4px solid #2196f3;
	padding: 15px;
	border-radius: 0 8px 8px 0;
	margin: 15px 0;
}

.info-box h4 {
	color: #1565c0;
	margin: 0 0 10px 0;
}

.config-table {
	width: 100%;
	border-collapse: collapse;
	margin: 15px 0;
}

.config-table th {
	background: #f5f5f5;
	padding: 12px;
	text-align: left;
	border: 1px solid #ddd;
	font-weight: 600;
}

.config-table td {
	padding: 12px;
	border: 1px solid #ddd;
	vertical-align: top;
}

.config-table tr:hover {
	background: #fafafa;
}

.badge {
	display: inline-block;
	padding: 3px 10px;
	border-radius: 12px;
	font-size: 12px;
	font-weight: 600;
}

.badge-required {
	background: #ffebee;
	color: #c62828;
}

.badge-optional {
	background: #e8f5e9;
	color: #2e7d32;
}

.badge-recommended {
	background: #e3f2fd;
	color: #1565c0;
}

.flow-diagram {
	background: #fafafa;
	border: 1px solid #e0e0e0;
	border-radius: 8px;
	padding: 20px;
	text-align: center;
	margin: 20px 0;
}

.flow-item {
	display: inline-block;
	background: #1e88e5;
	color: #fff;
	padding: 10px 20px;
	border-radius: 25px;
	margin: 5px;
	font-weight: 500;
}

.flow-arrow {
	display: inline-block;
	color: #666;
	font-size: 20px;
	margin: 0 10px;
}

.toc {
	background: #fafafa;
	border: 1px solid #e0e0e0;
	border-radius: 8px;
	padding: 20px;
	margin-bottom: 25px;
}

.toc h3 {
	margin-top: 0;
	color: #333;
}

.toc ul {
	list-style: none;
	padding-left: 0;
}

.toc li {
	padding: 8px 0;
	border-bottom: 1px solid #eee;
}

.toc li:last-child {
	border-bottom: none;
}

.toc a {
	color: #1e88e5;
	text-decoration: none;
	font-weight: 500;
}

.toc a:hover {
	text-decoration: underline;
}

.example-conversation {
	background: #fff;
	border: 1px solid #e0e0e0;
	border-radius: 8px;
	padding: 20px;
	margin: 15px 0;
}

.msg {
	padding: 10px 15px;
	border-radius: 15px;
	margin: 10px 0;
	max-width: 80%;
}

.msg-ai {
	background: #e3f2fd;
	color: #1565c0;
	margin-right: auto;
}

.msg-user {
	background: #f5f5f5;
	color: #333;
	margin-left: auto;
	text-align: right;
}

.msg-label {
	font-size: 11px;
	color: #666;
	margin-bottom: 5px;
}

.checklist {
	list-style: none;
	padding: 0;
}

.checklist li {
	padding: 10px 0;
	border-bottom: 1px solid #eee;
	display: flex;
	align-items: center;
}

.checklist li:before {
	content: '☐';
	margin-right: 10px;
	font-size: 18px;
	color: #999;
}

.checklist li.done:before {
	content: '✅';
}
</style>

<div class="help-container">

<!-- Índice -->
<div class="toc">
	<h3>📚 Índice do Guia</h3>
	<ul>
		<li><a href="#section-overview">1. Visão Geral do Sistema</a></li>
		<li><a href="#section-prereqs">2. Pré-requisitos</a></li>
		<li><a href="#section-providers">3. Configurar Provedores de IA</a></li>
		<li><a href="#section-secretary">4. Criar Secretária Virtual</a></li>
		<li><a href="#section-handoff">5. Configurar Handoff (Transferência)</a></li>
		<li><a href="#section-transfer-rules">6. Regras de Transferência por Departamento</a></li>
		<li><a href="#section-omniplay">7. Integração com OmniPlay</a></li>
		<li><a href="#section-dialplan">8. Configurar Dialplan no FreeSWITCH</a></li>
		<li><a href="#section-testing">9. Testar a Integração</a></li>
		<li><a href="#section-example">10. Exemplo Prático Completo</a></li>
		<li><a href="#section-troubleshooting">11. Solução de Problemas</a></li>
	</ul>
</div>

<!-- Section 1: Visão Geral -->
<div class="help-section" id="section-overview">
	<div class="help-section-header" onclick="toggleSection(this)">
		<span>📋 1. Visão Geral do Sistema</span>
		<span class="toggle-icon">▼</span>
	</div>
	<div class="help-section-content">
		<p>O <strong>Voice AI IVR</strong> é um sistema de atendimento por voz com Inteligência Artificial que se integra ao <strong>FusionPBX</strong> e ao <strong>OmniPlay</strong>.</p>
		
		<h4>🔄 Fluxo de uma Chamada</h4>
		<div class="flow-diagram">
			<span class="flow-item">📞 Cliente Liga</span>
			<span class="flow-arrow">→</span>
			<span class="flow-item">🔊 FreeSWITCH</span>
			<span class="flow-arrow">→</span>
			<span class="flow-item">🤖 Voice AI</span>
			<span class="flow-arrow">→</span>
			<span class="flow-item">🧠 IA (OpenAI/ElevenLabs)</span>
		</div>
		<div class="flow-diagram">
			<span class="flow-item">🤖 IA Responde</span>
			<span class="flow-arrow">→</span>
			<span class="flow-item">👤 Handoff?</span>
			<span class="flow-arrow">→</span>
			<span class="flow-item">📲 Transfere p/ Atendente</span>
			<span class="flow-arrow">→</span>
			<span class="flow-item">🎫 Cria Ticket OmniPlay</span>
		</div>
		
		<h4>🎯 Recursos Principais</h4>
		<table class="config-table">
			<tr>
				<th>Recurso</th>
				<th>Descrição</th>
				<th>Status</th>
			</tr>
			<tr>
				<td><strong>Secretária Virtual</strong></td>
				<td>IA que atende ligações, responde perguntas e encaminha chamadas</td>
				<td><span class="badge badge-required">✅ Ativo</span></td>
			</tr>
			<tr>
				<td><strong>Handoff Inteligente</strong></td>
				<td>Transferência para humanos quando necessário</td>
				<td><span class="badge badge-required">✅ Ativo</span></td>
			</tr>
			<tr>
				<td><strong>Regras de Transferência</strong></td>
				<td>Encaminhamento por departamento (Vendas, Suporte, Financeiro)</td>
				<td><span class="badge badge-required">✅ Ativo</span></td>
			</tr>
			<tr>
				<td><strong>Integração OmniPlay</strong></td>
				<td>Criação automática de tickets, callbacks, filas</td>
				<td><span class="badge badge-required">✅ Ativo</span></td>
			</tr>
			<tr>
				<td><strong>Callback</strong></td>
				<td>Agendamento de retorno quando atendentes não disponíveis</td>
				<td><span class="badge badge-required">✅ Ativo</span></td>
			</tr>
			<tr>
				<td><strong>Horário Comercial</strong></td>
				<td>Comportamento diferente fora do expediente</td>
				<td><span class="badge badge-required">✅ Ativo</span></td>
			</tr>
		</table>
	</div>
</div>

<!-- Section 2: Pré-requisitos -->
<div class="help-section" id="section-prereqs">
	<div class="help-section-header" onclick="toggleSection(this)">
		<span>📦 2. Pré-requisitos</span>
		<span class="toggle-icon">▼</span>
	</div>
	<div class="help-section-content">
		<h4>Checklist de Requisitos</h4>
		<ul class="checklist">
			<li>FusionPBX instalado e funcionando</li>
			<li>FreeSWITCH com mod_audio_stream compilado</li>
			<li>Docker e Docker Compose instalados</li>
			<li>Voice AI Service rodando (container voice-ai-realtime)</li>
			<li>OmniPlay backend acessível via rede</li>
			<li>Conta em provedor de IA (OpenAI, ElevenLabs ou Google)</li>
		</ul>
		
		<div class="warning-box">
			<h4>⚠️ mod_audio_stream é OBRIGATÓRIO (Não vem por padrão!)</h4>
			<p><strong>IMPORTANTE:</strong> O módulo <code>mod_audio_stream</code> <strong>NÃO é padrão</strong> do FreeSWITCH/FusionPBX. Ele precisa ser instalado manualmente!</p>
			<p>Este módulo permite enviar áudio da chamada via WebSocket para o servidor de IA.</p>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">A</span> Verificar se mod_audio_stream está instalado</h4>
			<div class="code-block">
<span class="comment"># No terminal do servidor, execute:</span>
fs_cli -x "module_exists mod_audio_stream"

<span class="comment"># Se retornar "false", o módulo NÃO está instalado!</span>
<span class="comment"># Se retornar "true", está OK ✅</span>
			</div>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">B</span> Instalar mod_audio_stream (se necessário)</h4>
			<p>O módulo está disponível em: <a href="https://github.com/amigniter/mod_audio_stream" target="_blank">github.com/amigniter/mod_audio_stream</a></p>
			<div class="code-block">
<span class="comment"># 1. Instalar dependências</span>
apt-get install -y libfreeswitch-dev libcurl4-openssl-dev libjsoncpp-dev

<span class="comment"># 2. Clonar e compilar</span>
cd /usr/src
git clone https://github.com/amigniter/mod_audio_stream.git
cd mod_audio_stream
make

<span class="comment"># 3. Instalar</span>
cp mod_audio_stream.so /usr/lib/freeswitch/mod/

<span class="comment"># 4. Habilitar no autoload</span>
nano /etc/freeswitch/autoload_configs/modules.conf.xml
<span class="comment"># Adicione: &lt;load module="mod_audio_stream"/&gt;</span>

<span class="comment"># 5. Carregar o módulo</span>
fs_cli -x "load mod_audio_stream"

<span class="comment"># 6. Verificar</span>
fs_cli -x "module_exists mod_audio_stream"
<span class="comment"># Deve retornar "true"</span>
			</div>
		</div>
		
		<h4>🐳 Verificar Container Voice AI</h4>
		<div class="code-block">
<span class="comment"># Verificar se o container está rodando</span>
docker ps | grep voice-ai

<span class="comment"># Ver logs do container</span>
docker compose logs -f voice-ai-realtime

<span class="comment"># Testar endpoint de saúde</span>
curl http://localhost:8085/health  <span class="comment"># Realtime (WebSocket)</span>
curl http://localhost:8100/health  <span class="comment"># API Principal</span>
		</div>
	</div>
</div>

<!-- Section 3: Provedores de IA -->
<div class="help-section" id="section-providers">
	<div class="help-section-header" onclick="toggleSection(this)">
		<span>🧠 3. Configurar Provedores de IA</span>
		<span class="toggle-icon">▼</span>
	</div>
	<div class="help-section-content">
		<p>Antes de criar uma secretária, configure pelo menos um provedor de IA.</p>
		
		<div class="step-box">
			<h4><span class="step-number">1</span> Acessar AI Providers</h4>
			<p>Vá em <strong>Voice Secretary → AI Providers → + Add</strong></p>
		</div>
		
		<h4>Provedores Suportados</h4>
		<table class="config-table">
			<tr>
				<th>Provedor</th>
				<th>Tipo</th>
				<th>Uso Recomendado</th>
			</tr>
			<tr>
				<td><strong>OpenAI Realtime</strong></td>
				<td>Realtime (voz nativa)</td>
				<td><span class="badge badge-recommended">Recomendado</span> - Melhor latência</td>
			</tr>
			<tr>
				<td><strong>ElevenLabs</strong></td>
				<td>Realtime (voz nativa)</td>
				<td>Vozes muito naturais em português</td>
			</tr>
			<tr>
				<td><strong>Google Gemini</strong></td>
				<td>Realtime</td>
				<td>Alternativa econômica</td>
			</tr>
			<tr>
				<td><strong>Google Speech-to-Text</strong></td>
				<td>STT</td>
				<td>Transcrição de áudio</td>
			</tr>
			<tr>
				<td><strong>OpenAI Whisper</strong></td>
				<td>STT</td>
				<td>Transcrição offline</td>
			</tr>
		</table>
		
		<div class="step-box">
			<h4><span class="step-number">2</span> Exemplo: Configurar OpenAI Realtime</h4>
			<table class="config-table">
				<tr><td><strong>Nome</strong></td><td>OpenAI Realtime GPT-4o</td></tr>
				<tr><td><strong>Tipo</strong></td><td>realtime</td></tr>
				<tr><td><strong>Provider</strong></td><td>openai_realtime</td></tr>
				<tr><td><strong>API Key</strong></td><td>sk-proj-xxxxxxxxxxxxxxxx</td></tr>
				<tr><td><strong>Model</strong></td><td>gpt-4o-realtime-preview</td></tr>
			</table>
		</div>
		
		<div class="info-box">
			<h4>💡 Dica: Vozes</h4>
			<p><strong>OpenAI:</strong> alloy, echo, fable, onyx, nova, shimmer</p>
			<p><strong>ElevenLabs:</strong> Crie vozes customizadas no painel ElevenLabs</p>
		</div>
	</div>
</div>

<!-- Section 4: Criar Secretária -->
<div class="help-section" id="section-secretary">
	<div class="help-section-header" onclick="toggleSection(this)">
		<span>🤖 4. Criar Secretária Virtual</span>
		<span class="toggle-icon">▼</span>
	</div>
	<div class="help-section-content">
		<div class="step-box">
			<h4><span class="step-number">1</span> Criar Nova Secretária</h4>
			<p>Vá em <strong>Voice Secretary → Secretaries → + Add</strong></p>
		</div>
		
		<h4>Campos Principais</h4>
		<table class="config-table">
			<tr>
				<th>Campo</th>
				<th>Exemplo</th>
				<th>Descrição</th>
			</tr>
			<tr>
				<td><strong>Nome</strong></td>
				<td>Atendente Virtual NetPlay</td>
				<td>Nome identificador da secretária</td>
			</tr>
			<tr>
				<td><strong>Nome da Empresa</strong></td>
				<td>NetPlay Internet</td>
				<td>Nome que a IA usará nas conversas</td>
			</tr>
			<tr>
				<td><strong>Ramal</strong></td>
				<td>8000</td>
				<td>Ramal que ativará a secretária</td>
			</tr>
			<tr>
				<td><strong>Provedor Realtime</strong></td>
				<td>OpenAI Realtime GPT-4o</td>
				<td>Provedor de IA configurado anteriormente</td>
			</tr>
			<tr>
				<td><strong>Idioma</strong></td>
				<td>pt-BR</td>
				<td>Português do Brasil</td>
			</tr>
		</table>
		
		<div class="step-box">
			<h4><span class="step-number">2</span> Prompt do Sistema (Exemplo)</h4>
			<div class="code-block">
Você é a assistente virtual da NetPlay Internet, uma provedora de internet fibra óptica.

REGRAS:
- Seja simpática, profissional e objetiva
- Sempre confirme o nome do cliente no início
- Para problemas técnicos, colete: nome, endereço e descrição do problema
- Para financeiro (boletos, faturas), transfira para o setor financeiro
- Para vendas de novos planos, transfira para o comercial

INFORMAÇÕES DA EMPRESA:
- Planos: 100MB (R$79,90), 200MB (R$99,90), 500MB (R$149,90)
- Horário: Seg-Sex 8h às 18h, Sáb 8h às 12h
- Suporte 24h para emergências

Quando não souber responder ou o cliente pedir para falar com atendente, 
transfira a ligação educadamente.
			</div>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">3</span> Mensagem de Saudação</h4>
			<div class="code-block">
Olá! Bem-vindo à NetPlay Internet. 
Meu nome é Ana, sou a assistente virtual. 
Como posso ajudar você hoje?
			</div>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">4</span> Mensagem de Despedida</h4>
			<div class="code-block">
Foi um prazer atendê-lo! 
Se precisar de mais alguma coisa, é só ligar novamente.
Tenha um ótimo dia!
			</div>
		</div>
	</div>
</div>

<!-- Section 5: Handoff -->
<div class="help-section" id="section-handoff">
	<div class="help-section-header" onclick="toggleSection(this)">
		<span>📞 5. Configurar Handoff (Transferência para Humano)</span>
		<span class="toggle-icon">▼</span>
	</div>
	<div class="help-section-content">
		<p>O <strong>Handoff</strong> é a transferência da chamada da IA para um atendente humano.</p>
		
		<h4>Quando acontece o Handoff?</h4>
		<ul>
			<li>Cliente pede para falar com atendente/humano/pessoa</li>
			<li>IA detecta frustração ou problema complexo</li>
			<li>Palavras-chave configuradas são detectadas</li>
			<li>Limite de turnos de conversa atingido</li>
		</ul>
		
		<div class="step-box">
			<h4><span class="step-number">1</span> Configurar Transfer Extension (Handoff Genérico)</h4>
			<p>Na edição da secretária, seção <strong>Transfer & Handoff Settings</strong>:</p>
			<table class="config-table">
				<tr>
					<td><strong>Transfer Extension</strong></td>
					<td>9000 (Ring Group de Atendimento)</td>
				</tr>
				<tr>
					<td><strong>Handoff Timeout</strong></td>
					<td>30 segundos</td>
				</tr>
				<tr>
					<td><strong>Handoff Keywords</strong></td>
					<td>atendente, humano, pessoa, operador, falar com alguém</td>
				</tr>
			</table>
		</div>
		
		<div class="warning-box">
			<h4>⚠️ Importante: Handoff Genérico vs Específico</h4>
			<p><strong>Transfer Extension</strong> = Handoff genérico (cliente diz "quero falar com alguém")</p>
			<p><strong>Regras de Transferência</strong> = Handoff específico (cliente diz "falar com financeiro")</p>
			<p>NÃO coloque nomes de departamentos nas Handoff Keywords!</p>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">2</span> Configurar Fallback (Se não atender)</h4>
			<table class="config-table">
				<tr>
					<td><strong>Fallback Action</strong></td>
					<td>Criar Ticket Pendente</td>
				</tr>
				<tr>
					<td><strong>Ticket Queue (ID)</strong></td>
					<td>1 (ID da fila no OmniPlay)</td>
				</tr>
				<tr>
					<td><strong>Fallback Priority</strong></td>
					<td>Média</td>
				</tr>
				<tr>
					<td><strong>Notificar Cliente</strong></td>
					<td>✅ Sim (envia WhatsApp)</td>
				</tr>
			</table>
		</div>
		
		<h4>🔄 Fluxo do Handoff</h4>
		<div class="flow-diagram">
			<span class="flow-item">Cliente pede humano</span>
			<span class="flow-arrow">→</span>
			<span class="flow-item">IA: "Transferindo..."</span>
			<span class="flow-arrow">→</span>
			<span class="flow-item">Música de espera</span>
			<span class="flow-arrow">→</span>
			<span class="flow-item">Ring para ramal</span>
		</div>
		<div class="flow-diagram">
			<span class="flow-item">Atendeu?</span>
			<span class="flow-arrow">→</span>
			<span class="flow-item">✅ Conecta</span>
			<span class="flow-arrow">ou</span>
			<span class="flow-item">❌ Fallback</span>
			<span class="flow-arrow">→</span>
			<span class="flow-item">Cria Ticket + WhatsApp</span>
		</div>
	</div>
</div>

<!-- Section 6: Transfer Rules -->
<div class="help-section" id="section-transfer-rules">
	<div class="help-section-header" onclick="toggleSection(this)">
		<span>🏢 6. Regras de Transferência por Departamento</span>
		<span class="toggle-icon">▼</span>
	</div>
	<div class="help-section-content">
		<p>Configure transferências específicas para departamentos como Vendas, Suporte, Financeiro.</p>
		
		<div class="step-box">
			<h4><span class="step-number">1</span> Acessar Regras de Transferência</h4>
			<p>Vá em <strong>Voice Secretary → Regras → + Add</strong></p>
		</div>
		
		<h4>Exemplo: Configurar Departamento Financeiro</h4>
		<table class="config-table">
			<tr>
				<td><strong>Departamento</strong></td>
				<td>Financeiro</td>
			</tr>
			<tr>
				<td><strong>Keywords</strong></td>
				<td>financeiro, boleto, fatura, pagamento, cobrança, segunda via, contas</td>
			</tr>
			<tr>
				<td><strong>Ramal/Fila</strong></td>
				<td>1004 (Jeni - Financeiro)</td>
			</tr>
			<tr>
				<td><strong>Timeout</strong></td>
				<td>25 segundos</td>
			</tr>
			<tr>
				<td><strong>Descrição</strong></td>
				<td>Setor responsável por boletos, faturas e cobranças</td>
			</tr>
		</table>
		
		<div class="step-box">
			<h4><span class="step-number">2</span> Exemplo: Departamento Comercial</h4>
			<table class="config-table">
				<tr>
					<td><strong>Departamento</strong></td>
					<td>Comercial</td>
				</tr>
				<tr>
					<td><strong>Keywords</strong></td>
					<td>vendas, comercial, novo plano, contratar, upgrade, proposta, orçamento</td>
				</tr>
				<tr>
					<td><strong>Ramal/Fila</strong></td>
					<td>9001 (Ring Group Comercial)</td>
				</tr>
			</table>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">3</span> Exemplo: Suporte Técnico</h4>
			<table class="config-table">
				<tr>
					<td><strong>Departamento</strong></td>
					<td>Suporte Técnico</td>
				</tr>
				<tr>
					<td><strong>Keywords</strong></td>
					<td>suporte, técnico, problema, internet, conexão, lento, caiu, não funciona</td>
				</tr>
				<tr>
					<td><strong>Ramal/Fila</strong></td>
					<td>5001 (Fila Call Center Suporte)</td>
				</tr>
			</table>
		</div>
		
		<div class="info-box">
			<h4>💡 Como a IA usa as Keywords</h4>
			<p>Quando o cliente diz algo como <em>"preciso falar sobre meu boleto"</em>, a IA detecta a keyword <strong>boleto</strong> e transfere automaticamente para o <strong>Financeiro</strong>.</p>
		</div>
	</div>
</div>

<!-- Section 7: OmniPlay -->
<div class="help-section" id="section-omniplay">
	<div class="help-section-header" onclick="toggleSection(this)">
		<span>🔗 7. Integração com OmniPlay</span>
		<span class="toggle-icon">▼</span>
	</div>
	<div class="help-section-content">
		<p>A integração com OmniPlay permite criar tickets automaticamente e sincronizar filas/usuários.</p>
		
		<div class="step-box">
			<h4><span class="step-number">1</span> Gerar Token no OmniPlay</h4>
			<ol>
				<li>Acesse o OmniPlay → <strong>Configurações → Integrações → Voice API</strong></li>
				<li>Clique em <strong>"Gerar Token"</strong></li>
				<li><strong>COPIE O TOKEN IMEDIATAMENTE</strong> (só aparece uma vez!)</li>
				<li>O token começa com <code>voice_</code></li>
			</ol>
		</div>
		
		<div class="warning-box">
			<h4>⚠️ Token de Uso Único</h4>
			<p>O token só é exibido UMA VEZ após ser gerado. Se perder, será necessário gerar um novo (o anterior será invalidado).</p>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">2</span> Configurar no FusionPBX</h4>
			<p>Vá em <strong>Voice Secretary → 🔗 OmniPlay</strong></p>
			<table class="config-table">
				<tr>
					<td><strong>URL da API</strong></td>
					<td>https://api.seuomniplay.com.br</td>
				</tr>
				<tr>
					<td><strong>Token de API</strong></td>
					<td>voice_xxxxxxxxxxxxxxxxxxxxxxxx</td>
				</tr>
				<tr>
					<td><strong>Sincronização Automática</strong></td>
					<td>✅ Habilitado, a cada 5 minutos</td>
				</tr>
			</table>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">3</span> Testar Conexão</h4>
			<p>Clique em <strong>"🔌 Testar Conexão"</strong></p>
			<p>Se OK, o ID da empresa será preenchido automaticamente.</p>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">4</span> Sincronizar Dados</h4>
			<p>Clique em <strong>"🔄 Forçar Sincronização"</strong></p>
			<p>Isso baixa as filas e usuários do OmniPlay para usar nos campos de fallback.</p>
		</div>
		
		<div class="success-box">
			<h4>✅ Após integrar</h4>
			<ul>
				<li>Tickets de callback são criados automaticamente</li>
				<li>Transcrições das ligações são salvas no ticket</li>
				<li>Cliente pode receber WhatsApp quando não há atendente</li>
			</ul>
		</div>
	</div>
</div>

<!-- Section 8: Dialplan -->
<div class="help-section" id="section-dialplan">
	<div class="help-section-header" onclick="toggleSection(this)">
		<span>📋 8. Configurar Dialplan no FreeSWITCH (PASSO A PASSO)</span>
		<span class="toggle-icon">▼</span>
	</div>
	<div class="help-section-content">
		<p>O dialplan conecta as chamadas ao Voice AI Service. Usamos a <strong>Arquitetura Híbrida</strong>:</p>
		<ul>
			<li><strong>ESL (porta 8022)</strong> → Controle da chamada (transfer, hangup, hold)</li>
			<li><strong>mod_audio_stream (porta 8085)</strong> → Transporte de áudio via WebSocket</li>
		</ul>
		
		<div class="warning-box">
			<h4>⚠️ PRÉ-REQUISITO CRÍTICO: mod_audio_stream</h4>
			<p>A ação <code>audio_stream</code> <strong>NÃO é padrão</strong> do FreeSWITCH. Ela só funciona se o módulo <code>mod_audio_stream</code> estiver instalado!</p>
			<p>Verifique antes de configurar o dialplan:</p>
			<div class="code-block" style="margin: 10px 0;">
fs_cli -x "module_exists mod_audio_stream"
<span class="comment"># Deve retornar "true"</span>
			</div>
			<p>Se retornar "false", <strong>veja a seção 2 (Pré-requisitos)</strong> para instruções de instalação.</p>
		</div>
		
		<div class="info-box">
			<h4>🎯 Por que Arquitetura Híbrida?</h4>
			<p>O <strong>WebSocket resolve o problema de NAT</strong> automaticamente. Clientes atrás de roteadores funcionam sem configuração adicional. O ESL permite controle avançado (transferir, desligar, colocar em espera).</p>
		</div>
		
		<hr style="margin: 25px 0; border: 1px dashed #ddd;">
		
		<div class="step-box">
			<h4><span class="step-number">1</span> Acessar Dialplan Manager</h4>
			<p>No FusionPBX, vá em: <strong>Dialplan → Dialplan Manager</strong></p>
			<div style="background: #f0f0f0; padding: 15px; border-radius: 8px; font-family: monospace; margin: 10px 0;">
				📁 <strong>Dialplan</strong><br>
				&nbsp;&nbsp;&nbsp;├── 📄 Dialplan Manager 👈 <em>Clique aqui</em><br>
				&nbsp;&nbsp;&nbsp;├── 📄 Inbound Routes<br>
				&nbsp;&nbsp;&nbsp;└── 📄 Outbound Routes
			</div>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">2</span> Criar Novo Dialplan</h4>
			<p>Clique no botão <strong>+ Add</strong> (canto superior direito)</p>
			<div style="background: #e3f2fd; padding: 15px; border-radius: 8px; margin: 10px 0;">
				<p style="margin: 0;"><strong>🔵 Botão [+ Add]</strong> → Abre formulário de novo dialplan</p>
			</div>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">3</span> Preencher Informações Básicas</h4>
			<p>Preencha os campos conforme a tabela abaixo:</p>
			<table class="config-table">
				<tr>
					<th>Campo</th>
					<th>Valor</th>
					<th>Explicação</th>
				</tr>
				<tr>
					<td><strong>Name</strong></td>
					<td><code>voice_ai_hybrid_8000</code></td>
					<td>Nome identificador do dialplan</td>
				</tr>
				<tr>
					<td><strong>Number</strong></td>
					<td><code>8000</code></td>
					<td>Ramal que ativará a secretária virtual</td>
				</tr>
				<tr>
					<td><strong>Context</strong></td>
					<td><code>${domain_name}</code></td>
					<td>Ou o nome do seu domínio (ex: empresa.com.br)</td>
				</tr>
				<tr>
					<td><strong>Order</strong></td>
					<td><code>100</code></td>
					<td>Prioridade (número baixo = executa primeiro)</td>
				</tr>
				<tr>
					<td><strong>Enabled</strong></td>
					<td><code>true</code></td>
					<td>Dialplan ativo</td>
				</tr>
				<tr>
					<td><strong>Continue</strong></td>
					<td><code>false</code></td>
					<td>⚠️ <strong>CRÍTICO:</strong> Deve ser false!</td>
				</tr>
				<tr>
					<td><strong>Description</strong></td>
					<td><code>Voice AI - Secretária Virtual</code></td>
					<td>Descrição para identificação</td>
				</tr>
			</table>
			
			<div class="warning-box">
				<h4>⚠️ Continue DEVE ser false</h4>
				<p>Se <code>Continue</code> for <code>true</code>, o FreeSWITCH continuará processando outros dialplans após o nosso, causando comportamento inesperado. <strong>Sempre defina como false!</strong></p>
			</div>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">4</span> Adicionar Condição (Condition)</h4>
			<p>Role até a seção <strong>"Dialplan Details"</strong> e clique em <strong>+ Add</strong></p>
			<table class="config-table">
				<tr>
					<th>Campo</th>
					<th>Valor</th>
				</tr>
				<tr>
					<td><strong>Tag</strong></td>
					<td><code>condition</code></td>
				</tr>
				<tr>
					<td><strong>Type</strong></td>
					<td><code>destination_number</code></td>
				</tr>
				<tr>
					<td><strong>Data</strong></td>
					<td><code>^8000$</code></td>
				</tr>
				<tr>
					<td><strong>Order</strong></td>
					<td><code>0</code></td>
				</tr>
			</table>
			<p><em>Isso significa: "Execute as ações abaixo quando alguém ligar para 8000"</em></p>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">5</span> Adicionar Ações (IMPORTANTE: Ordem correta!)</h4>
			<p>Adicione as seguintes ações <strong>na ordem exata</strong> listada:</p>
			
			<table class="config-table">
				<tr>
					<th>Ordem</th>
					<th>Tag</th>
					<th>Type</th>
					<th>Data</th>
					<th>O que faz</th>
				</tr>
				<tr style="background: #fff9e6;">
					<td><strong>1</strong></td>
					<td>action</td>
					<td><code>set</code></td>
					<td><code style="word-break: break-all;">VOICE_AI_SECRETARY_UUID=<span style="color: red;">SEU-UUID-AQUI</span></code></td>
					<td>🔑 Identifica qual secretária usar</td>
				</tr>
				<tr style="background: #fff9e6;">
					<td><strong>2</strong></td>
					<td>action</td>
					<td><code>set</code></td>
					<td><code>VOICE_AI_DOMAIN_UUID=${domain_uuid}</code></td>
					<td>🏢 Passa o domínio para o Voice AI</td>
				</tr>
				<tr style="background: #ffebee;">
					<td><strong>3</strong></td>
					<td>action</td>
					<td><code>set</code></td>
					<td><code>STREAM_PLAYBACK=true</code></td>
					<td>🔊 <strong>CRÍTICO:</strong> Habilita playback bidirecional</td>
				</tr>
				<tr style="background: #fce4ec;">
					<td><strong>4</strong></td>
					<td>action</td>
					<td><code>set</code></td>
					<td><code>jitterbuffer_msec=100:300:40</code></td>
					<td>🎚️ Configura jitter buffer (evita áudio picotado)</td>
				</tr>
				<tr style="background: #e3f2fd;">
					<td><strong>5</strong></td>
					<td>action</td>
					<td><code>set</code></td>
					<td><code style="font-size: 10px;">api_on_answer=uuid_audio_stream ${uuid} start ws://127.0.0.1:8085/stream/${VOICE_AI_SECRETARY_UUID}/${uuid}/${caller_id_number} mono 16k</code></td>
					<td>🎙️ Configura streaming (executa após answer)</td>
				</tr>
				<tr style="background: #e8f5e9;">
					<td><strong>6</strong></td>
					<td>action</td>
					<td><code>answer</code></td>
					<td><em>(deixe vazio)</em></td>
					<td>📞 Atende a chamada (dispara api_on_answer)</td>
				</tr>
				<tr style="background: #e3f2fd;">
					<td><strong>7</strong></td>
					<td>action</td>
					<td><code>socket</code></td>
					<td><code>127.0.0.1:8022 async full</code></td>
					<td>🔌 Conecta ESL para controle</td>
				</tr>
				<tr style="background: #f5f5f5;">
					<td><strong>8</strong></td>
					<td>action</td>
					<td><code>park</code></td>
					<td><em>(deixe vazio)</em></td>
					<td>⏸️ Mantém chamada ativa</td>
				</tr>
			</table>
		</div>
		
		<div class="info-box">
			<h4>💡 Como obter o UUID da Secretária</h4>
			<p>1. Vá em <strong>Voice Secretary → Secretaries</strong></p>
			<p>2. Clique em uma secretária para editar</p>
			<p>3. O UUID está na URL do navegador:</p>
			<div class="code-block" style="word-break: break-all;">
/app/voice_secretary/secretary_edit.php?id=<span style="color: #aed581; font-weight: bold;">dc923a2f-b88a-4a2f-8029-d6e0c06893c5</span>

<span class="comment"># Copie apenas o UUID (parte após id=)</span>
			</div>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">6</span> Salvar e Verificar</h4>
			<p>Clique no botão <strong>Save</strong>. Seu dialplan deve ficar assim na lista:</p>
			<div style="background: #f5f5f5; padding: 15px; border-radius: 8px; font-family: monospace; margin: 10px 0; border: 1px solid #ddd;">
				<table style="width: 100%; border-collapse: collapse;">
					<tr style="background: #e0e0e0;">
						<td style="padding: 8px; border-bottom: 1px solid #ccc;">✅</td>
						<td style="padding: 8px; border-bottom: 1px solid #ccc;"><strong>voice_ai_hybrid_8000</strong></td>
						<td style="padding: 8px; border-bottom: 1px solid #ccc;">8000</td>
						<td style="padding: 8px; border-bottom: 1px solid #ccc;">empresa.com.br</td>
						<td style="padding: 8px; border-bottom: 1px solid #ccc;">100</td>
					</tr>
				</table>
			</div>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">7</span> Recarregar Dialplan no FreeSWITCH</h4>
			<p>Execute no terminal do servidor:</p>
			<div class="code-block">
<span class="comment"># Opção 1: Via fs_cli</span>
fs_cli -x "reloadxml"

<span class="comment"># Opção 2: Via SSH direto</span>
/usr/local/freeswitch/bin/fs_cli -x "reloadxml"

<span class="comment"># Verificar se dialplan foi carregado</span>
fs_cli -x "show dialplan" | grep voice_ai
			</div>
		</div>
		
		<div class="success-box">
			<h4>✅ Dialplan Configurado com Sucesso!</h4>
			<p>Agora, quando alguém ligar para <strong>8000</strong>, a chamada será:</p>
			<ol>
				<li>Atendida automaticamente</li>
				<li>Conectada ao Voice AI via ESL (controle)</li>
				<li>Stream de áudio iniciado via WebSocket</li>
				<li>Mantida ativa até a IA ou handoff encerrar</li>
			</ol>
		</div>
		
		<hr style="margin: 25px 0; border: 1px dashed #ddd;">
		
		<h4>📄 Visualização do XML Gerado</h4>
		<p>O FusionPBX gera automaticamente este XML (para referência):</p>
		<div class="code-block" style="font-size: 12px;">
<span class="comment">&lt;!-- Dialplan gerado pelo FusionPBX --&gt;</span>
&lt;extension name="voice_ai_hybrid_8000" continue="false"&gt;
  &lt;condition field="destination_number" expression="^8000$"&gt;
    <span class="comment">&lt;!-- 1. Identificação da secretária e domínio --&gt;</span>
    &lt;action application="set" data="VOICE_AI_SECRETARY_UUID=dc923a2f-b88a-4a2f-8029-d6e0c06893c5"/&gt;
    &lt;action application="set" data="VOICE_AI_DOMAIN_UUID=${domain_uuid}"/&gt;
    
    <span class="comment">&lt;!-- 2. CRÍTICO: Habilitar playback bidirecional --&gt;</span>
    &lt;action application="set" data="STREAM_PLAYBACK=true"/&gt;
    
    <span class="comment">&lt;!-- 3. Configurar jitter buffer para evitar áudio picotado --&gt;</span>
    &lt;action application="set" data="jitterbuffer_msec=100:300:40"/&gt;
    
    <span class="comment">&lt;!-- 4. Configurar streaming via api_on_answer (ANTES do answer!) --&gt;</span>
    <span class="comment">&lt;!-- O comando será executado APÓS o answer, automaticamente --&gt;</span>
    &lt;action application="set" data="api_on_answer=uuid_audio_stream ${uuid} start ws://127.0.0.1:8085/stream/${VOICE_AI_SECRETARY_UUID}/${uuid}/${caller_id_number} mono 16k"/&gt;
    
    <span class="comment">&lt;!-- 5. Atender a chamada (dispara api_on_answer) --&gt;</span>
    &lt;action application="answer"/&gt;
    
    <span class="comment">&lt;!-- 6. Conectar ESL para CONTROLE (transferências, hangup, etc) --&gt;</span>
    &lt;action application="socket" data="127.0.0.1:8022 async full"/&gt;
    
    <span class="comment">&lt;!-- 7. Manter chamada ativa --&gt;</span>
    &lt;action application="park"/&gt;
  &lt;/condition&gt;
&lt;/extension&gt;
		</div>
		
		<div class="info-box">
			<h4>📝 Parâmetros do uuid_audio_stream</h4>
			<table class="config-table">
				<tr><th>Parâmetro</th><th>Valores</th><th>Descrição</th></tr>
				<tr><td><code>${uuid}</code></td><td>Variável automática</td><td>UUID da chamada atual</td></tr>
				<tr><td><code>start</code></td><td>start / stop / pause / resume</td><td>Ação a executar</td></tr>
				<tr><td><code>ws://...</code></td><td>ws:// ou wss://</td><td>URL do servidor WebSocket</td></tr>
				<tr><td><code>mixed</code></td><td><strong>mono / mixed / stereo</strong></td><td>⚠️ NÃO use "both"!</td></tr>
				<tr><td><code>16k</code></td><td><strong>8k / 16k</strong></td><td>⚠️ Use "k" (ex: 16k, NÃO 16000)</td></tr>
			</table>
		</div>
		
		<div class="warning-box">
			<h4>⚠️ Ordem Crítica das Ações</h4>
			<p><code>api_on_answer</code> deve ser definido <strong>ANTES</strong> do <code>answer</code>!</p>
			<p>Isso porque o <code>api_on_answer</code> é um comando que será <strong>executado automaticamente</strong> quando o <code>answer</code> for chamado.</p>
		</div>
		
		<h4>🔧 Troubleshooting do Dialplan</h4>
		<div class="warning-box">
			<h4>❌ "audio_stream" não reconhecido</h4>
			<p><strong>Causa:</strong> mod_audio_stream não está carregado.</p>
			<p><strong>Solução:</strong></p>
			<div class="code-block">
<span class="comment"># Verificar se módulo existe</span>
fs_cli -x "module_exists mod_audio_stream"

<span class="comment"># Se retornar false, carregar o módulo</span>
fs_cli -x "load mod_audio_stream"

<span class="comment"># Para carregar automaticamente, edite modules.conf.xml</span>
nano /etc/freeswitch/autoload_configs/modules.conf.xml
<span class="comment"># Adicione: &lt;load module="mod_audio_stream"/&gt;</span>
			</div>
		</div>
		
		<div class="warning-box">
			<h4>❌ Chamada não conecta ao Voice AI</h4>
			<p><strong>Causa:</strong> Container não está rodando ou portas não estão abertas.</p>
			<p><strong>Solução:</strong></p>
			<div class="code-block">
<span class="comment"># Verificar container</span>
docker ps | grep voice-ai

<span class="comment"># Verificar portas</span>
netstat -tlnp | grep -E "(8022|8085)"

<span class="comment"># Testar conexão ESL</span>
telnet 127.0.0.1 8022
			</div>
		</div>
	</div>
</div>

<!-- Section 9: Testing -->
<div class="help-section" id="section-testing">
	<div class="help-section-header" onclick="toggleSection(this)">
		<span>🧪 9. Testar a Integração</span>
		<span class="toggle-icon">▼</span>
	</div>
	<div class="help-section-content">
		<div class="step-box">
			<h4><span class="step-number">1</span> Verificar Container Voice AI</h4>
			<div class="code-block">
<span class="comment"># Container rodando?</span>
docker ps | grep voice-ai

<span class="comment"># Health check - API HTTP (porta 8100)</span>
curl http://localhost:8100/health
<span class="comment"># Deve retornar: {"status":"healthy","service":"voice-ai-service"...}</span>

<span class="comment"># Ver logs em tempo real</span>
docker compose logs -f voice-ai-realtime
			</div>
		</div>
		
		<div class="info-box">
			<h4>📡 Portas do Voice AI</h4>
			<table class="config-table">
				<tr><th>Porta</th><th>Protocolo</th><th>Função</th></tr>
				<tr><td><strong>8100</strong></td><td>HTTP</td><td>API REST (health, métricas, configurações)</td></tr>
				<tr><td><strong>8085</strong></td><td>WebSocket</td><td>Stream de áudio (mod_audio_stream)</td></tr>
				<tr><td><strong>8022</strong></td><td>TCP</td><td>ESL Outbound (controle de chamada)</td></tr>
			</table>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">2</span> Testar Conexão ESL</h4>
			<div class="code-block">
<span class="comment"># No FreeSWITCH, verificar conexões ESL</span>
fs_cli -x "show sockets"
			</div>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">3</span> Fazer Ligação de Teste</h4>
			<ol>
				<li>Registre um softphone (ex: MicroSIP, Zoiper)</li>
				<li>Disque o ramal configurado (ex: 8000)</li>
				<li>Aguarde a saudação da IA</li>
				<li>Faça uma pergunta simples</li>
				<li>Peça para "falar com atendente" (testar handoff)</li>
			</ol>
		</div>
		
		<div class="step-box">
			<h4><span class="step-number">4</span> Verificar Logs</h4>
			<div class="code-block">
<span class="comment"># Log do Voice AI</span>
docker compose logs -f voice-ai-realtime | grep -E "(session|handoff|error)"

<span class="comment"># Log do FreeSWITCH</span>
tail -f /var/log/freeswitch/freeswitch.log | grep -i voice
			</div>
		</div>
		
		<div class="success-box">
			<h4>✅ Teste Bem-Sucedido</h4>
			<ul>
				<li>IA responde à saudação</li>
				<li>IA entende perguntas em português</li>
				<li>Handoff transfere para o ramal correto</li>
				<li>Se não atender, ticket é criado no OmniPlay</li>
			</ul>
		</div>
	</div>
</div>

<!-- Section 10: Exemplo Prático -->
<div class="help-section" id="section-example">
	<div class="help-section-header" onclick="toggleSection(this)">
		<span>🎯 10. Exemplo Prático Completo</span>
		<span class="toggle-icon">▼</span>
	</div>
	<div class="help-section-content">
		<h4>Cenário: Provedor de Internet NetPlay</h4>
		<p>Vamos configurar uma secretária completa para uma empresa de internet.</p>
		
		<h4>📋 Configuração da Secretária</h4>
		<table class="config-table">
			<tr><td><strong>Nome</strong></td><td>Ana - Atendente Virtual NetPlay</td></tr>
			<tr><td><strong>Empresa</strong></td><td>NetPlay Internet</td></tr>
			<tr><td><strong>Ramal</strong></td><td>8000</td></tr>
			<tr><td><strong>Provedor</strong></td><td>OpenAI Realtime GPT-4o</td></tr>
			<tr><td><strong>Voz</strong></td><td>nova (feminina, amigável)</td></tr>
			<tr><td><strong>Idioma</strong></td><td>pt-BR</td></tr>
		</table>
		
		<h4>📞 Configuração de Handoff</h4>
		<table class="config-table">
			<tr><td><strong>Transfer Extension</strong></td><td>9000 (Fila Geral de Atendimento)</td></tr>
			<tr><td><strong>Timeout</strong></td><td>30 segundos</td></tr>
			<tr><td><strong>Keywords</strong></td><td>atendente, humano, pessoa, operador</td></tr>
			<tr><td><strong>Fallback</strong></td><td>Criar Ticket + WhatsApp</td></tr>
			<tr><td><strong>Fila OmniPlay</strong></td><td>ID: 1 (Atendimento Geral)</td></tr>
		</table>
		
		<h4>🏢 Regras de Transferência</h4>
		<table class="config-table">
			<tr>
				<th>Departamento</th>
				<th>Keywords</th>
				<th>Ramal/Fila</th>
			</tr>
			<tr>
				<td>Financeiro</td>
				<td>boleto, fatura, pagamento, cobrança</td>
				<td>1004 (Jeni)</td>
			</tr>
			<tr>
				<td>Comercial</td>
				<td>vendas, novo plano, contratar, upgrade</td>
				<td>9001 (Ring Group)</td>
			</tr>
			<tr>
				<td>Suporte</td>
				<td>suporte, técnico, problema, internet caiu</td>
				<td>5001 (Call Center)</td>
			</tr>
			<tr>
				<td>Cancelamento</td>
				<td>cancelar, cancelamento, encerrar contrato</td>
				<td>1010 (Retenção)</td>
			</tr>
		</table>
		
		<h4>💬 Exemplo de Conversa</h4>
		<div class="example-conversation">
			<div class="msg msg-ai">
				<div class="msg-label">🤖 Ana (IA)</div>
				Olá! Bem-vindo à NetPlay Internet. Meu nome é Ana, sou a assistente virtual. Como posso ajudar você hoje?
			</div>
			
			<div class="msg msg-user">
				<div class="msg-label">👤 Cliente</div>
				Oi, minha internet está muito lenta hoje
			</div>
			
			<div class="msg msg-ai">
				<div class="msg-label">🤖 Ana (IA)</div>
				Entendo, sinto muito pelo inconveniente! Posso ajudar a verificar isso. Qual é o seu nome completo e o endereço da instalação?
			</div>
			
			<div class="msg msg-user">
				<div class="msg-label">👤 Cliente</div>
				João Silva, Rua das Flores, 123
			</div>
			
			<div class="msg msg-ai">
				<div class="msg-label">🤖 Ana (IA)</div>
				Obrigada, João! Deixa eu verificar... Vejo que seu plano é de 200MB. Você já tentou reiniciar o roteador?
			</div>
			
			<div class="msg msg-user">
				<div class="msg-label">👤 Cliente</div>
				Já tentei, não resolveu. Quero falar com o suporte técnico
			</div>
			
			<div class="msg msg-ai">
				<div class="msg-label">🤖 Ana (IA) → Detectou "suporte técnico"</div>
				Sem problemas, João! Vou transferir você para nosso suporte técnico agora mesmo. Por favor, aguarde um momento.
			</div>
			
			<div class="msg msg-ai" style="background: #e8f5e9;">
				<div class="msg-label">🔄 Sistema</div>
				<em>Transferindo para ramal 5001 (Suporte Técnico)...</em><br>
				<em>🎵 Música de espera...</em><br>
				<em>✅ Técnico atendeu!</em>
			</div>
		</div>
		
		<h4>📊 Fluxo Completo com Fallback</h4>
		<div class="example-conversation">
			<div class="msg msg-user">
				<div class="msg-label">👤 Cliente</div>
				Preciso da segunda via do boleto
			</div>
			
			<div class="msg msg-ai">
				<div class="msg-label">🤖 Ana (IA) → Detectou "boleto"</div>
				Claro! Vou transferir você para o setor financeiro. Um momento, por favor.
			</div>
			
			<div class="msg msg-ai" style="background: #fff3e0;">
				<div class="msg-label">⏳ Sistema</div>
				<em>Transferindo para ramal 1004 (Financeiro)...</em><br>
				<em>🎵 Música de espera... (30 segundos)</em><br>
				<em>❌ Ninguém atendeu!</em>
			</div>
			
			<div class="msg msg-ai">
				<div class="msg-label">🤖 Ana (IA)</div>
				João, no momento o setor financeiro está ocupado. Posso criar um chamado para retornarem sua ligação. Qual o melhor telefone para contato?
			</div>
			
			<div class="msg msg-user">
				<div class="msg-label">👤 Cliente</div>
				11 99999-8888
			</div>
			
			<div class="msg msg-ai">
				<div class="msg-label">🤖 Ana (IA)</div>
				Perfeito! Criei um chamado de callback com prioridade alta. O setor financeiro vai retornar para (11) 99999-8888 o mais breve possível. Você também receberá uma confirmação por WhatsApp. Posso ajudar em mais alguma coisa?
			</div>
			
			<div class="msg msg-ai" style="background: #e8f5e9;">
				<div class="msg-label">✅ Sistema</div>
				<em>Ticket #12345 criado no OmniPlay</em><br>
				<em>📱 WhatsApp enviado para cliente</em><br>
				<em>🔔 Notificação enviada para fila Financeiro</em>
			</div>
		</div>
	</div>
</div>

<!-- Section 11: Troubleshooting -->
<div class="help-section" id="section-troubleshooting">
	<div class="help-section-header" onclick="toggleSection(this)">
		<span>🔧 11. Solução de Problemas</span>
		<span class="toggle-icon">▼</span>
	</div>
	<div class="help-section-content">
		<h4>Problemas Comuns</h4>
		
		<div class="warning-box">
			<h4>❌ "no such table: v_voice_secretaries"</h4>
			<p><strong>Causa:</strong> Migrations não foram executadas.</p>
			<p><strong>Solução:</strong></p>
			<div class="code-block">
cd /root/voice-ai-ivr/database/migrations
for f in *.sql; do sudo -u postgres psql fusionpbx -f "$f"; done
			</div>
		</div>
		
		<div class="warning-box">
			<h4>❌ Chamada não conecta à IA</h4>
			<p><strong>Causa:</strong> ESL não está configurado ou container não está rodando.</p>
			<p><strong>Solução:</strong></p>
			<div class="code-block">
<span class="comment"># Verificar container</span>
docker ps | grep voice-ai

<span class="comment"># Verificar porta ESL</span>
netstat -tlnp | grep 8022

<span class="comment"># Verificar logs</span>
docker compose logs voice-ai-realtime
			</div>
		</div>
		
		<div class="warning-box">
			<h4>❌ IA não responde / Timeout</h4>
			<p><strong>Causa:</strong> API key inválida ou provedor fora do ar.</p>
			<p><strong>Solução:</strong></p>
			<ol>
				<li>Verificar API key no provedor configurado</li>
				<li>Testar API key diretamente no site do provedor</li>
				<li>Verificar logs para erros de autenticação</li>
			</ol>
		</div>
		
		<div class="warning-box">
			<h4>❌ Handoff não funciona</h4>
			<p><strong>Causa:</strong> Ramal de transferência incorreto ou não existe.</p>
			<p><strong>Solução:</strong></p>
			<ol>
				<li>Verificar se o ramal/ring group existe no FusionPBX</li>
				<li>Testar ligação direta para o ramal</li>
				<li>Verificar se o ramal está registrado</li>
			</ol>
		</div>
		
		<div class="warning-box">
			<h4>❌ Ticket não é criado no OmniPlay</h4>
			<p><strong>Causa:</strong> Token inválido ou URL incorreta.</p>
			<p><strong>Solução:</strong></p>
			<ol>
				<li>Ir em 🔗 OmniPlay e clicar "Testar Conexão"</li>
				<li>Verificar se o token não expirou</li>
				<li>Gerar novo token se necessário</li>
			</ol>
		</div>
		
		<h4>📞 Suporte</h4>
		<div class="info-box">
			<p>Se precisar de ajuda adicional:</p>
			<ul>
				<li>Documentação: <code>/root/voice-ai-ivr/docs/</code></li>
				<li>Logs: <code>docker compose logs -f voice-ai-realtime</code></li>
				<li>GitHub Issues: Abrir ticket no repositório</li>
			</ul>
		</div>
	</div>
</div>

</div><!-- end help-container -->

<script>
function toggleSection(header) {
	var content = header.nextElementSibling;
	var icon = header.querySelector('.toggle-icon');
	
	if (content.classList.contains('hidden')) {
		content.classList.remove('hidden');
		header.classList.remove('collapsed');
	} else {
		content.classList.add('hidden');
		header.classList.add('collapsed');
	}
}

// Smooth scroll para links do índice
document.querySelectorAll('.toc a').forEach(function(link) {
	link.addEventListener('click', function(e) {
		e.preventDefault();
		var targetId = this.getAttribute('href').substring(1);
		var target = document.getElementById(targetId);
		if (target) {
			target.scrollIntoView({ behavior: 'smooth', block: 'start' });
			// Abrir a seção se estiver fechada
			var content = target.querySelector('.help-section-content');
			var header = target.querySelector('.help-section-header');
			if (content && content.classList.contains('hidden')) {
				content.classList.remove('hidden');
				header.classList.remove('collapsed');
			}
		}
	});
});
</script>

<?php
require_once "resources/footer.php";
?>
