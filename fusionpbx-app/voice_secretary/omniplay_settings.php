<?php
/**
 * OmniPlay Integration Settings
 * 
 * Página para configurar a integração FusionPBX ↔ OmniPlay
 * Permite buscar filas e usuários do OmniPlay automaticamente.
 * 
 * @author OmniPlay Team
 * @since 2026-01-17
 */

// Includes padrão do FusionPBX
// ✅ FIX: Usar dirname(__DIR__, 2) em vez de root.php (padrão FusionPBX)
require_once dirname(__DIR__, 2) . "/resources/require.php";

// Check auth (já incluído no require.php, mas garantir)
if (!isset($_SESSION['domain_uuid'])) {
	echo "access denied";
	exit;
}

// Verificar permissão
if (!permission_exists('voice_secretary_edit')) {
	echo "access denied";
	exit;
}

// Add multi-lingual support
$language = new text;
$text = $language->get();

// Carregar classe do cliente OmniPlay
require_once "resources/classes/omniplay_api_client.php";

// Variáveis
$domain_uuid = $_SESSION['domain_uuid'];
$action = $_REQUEST['action'] ?? '';

// ✅ FIX: No FusionPBX, a conexão PDO está em $database->db (não $db)
if (!isset($database) || !is_object($database)) {
	$database = new database;
}
$db = $database->db;

// Buscar configurações atuais
$settings = [];
try {
	$sql = "SELECT * FROM v_voice_omniplay_settings WHERE domain_uuid = :domain_uuid LIMIT 1";
	$stmt = $db->prepare($sql);
	$stmt->execute([':domain_uuid' => $domain_uuid]);
	$settings = $stmt->fetch(PDO::FETCH_ASSOC) ?: [];
} catch (PDOException $e) {
	// Tabela pode não existir ainda (migration não rodada)
	error_log("OmniPlay settings table may not exist: " . $e->getMessage());
}

// Processar formulário
if ($_SERVER['REQUEST_METHOD'] === 'POST' && !empty($_POST)) {
	// Validar CSRF
	$token = new token;
	if (!$token->validate($_SERVER['PHP_SELF'])) {
		message::add($text['message-csrf_failed'], 'negative');
		header("Location: omniplay_settings.php");
		exit;
	}
	
	// Obter dados do formulário
	$omniplay_api_url = trim($_POST['omniplay_api_url'] ?? '');
	$omniplay_api_token = trim($_POST['omniplay_api_token'] ?? '');
	$omniplay_company_id = !empty($_POST['omniplay_company_id']) ? intval($_POST['omniplay_company_id']) : null;
	$auto_sync_enabled = isset($_POST['auto_sync_enabled']) ? 't' : 'f';  // PostgreSQL boolean
	$sync_interval_minutes = intval($_POST['sync_interval_minutes'] ?? 5);
	
	// Validar URL
	if (!empty($omniplay_api_url) && !filter_var($omniplay_api_url, FILTER_VALIDATE_URL)) {
		message::add("URL inválida. Use formato: https://api.omniplay.com.br", 'negative');
		header("Location: omniplay_settings.php");
		exit;
	}
	
	// Remover barra final da URL
	$omniplay_api_url = rtrim($omniplay_api_url, '/');
	
	// ✅ FIX: Validar sync_interval_minutes (1-60)
	if ($sync_interval_minutes < 1) {
		$sync_interval_minutes = 1;
	} elseif ($sync_interval_minutes > 60) {
		$sync_interval_minutes = 60;
	}
	
	try {
		if (!empty($settings)) {
			// Atualizar
			$sql = "UPDATE v_voice_omniplay_settings SET 
				omniplay_api_url = :omniplay_api_url,
				omniplay_api_token = :omniplay_api_token,
				omniplay_company_id = :omniplay_company_id,
				auto_sync_enabled = :auto_sync_enabled,
				sync_interval_minutes = :sync_interval_minutes,
				updated_at = NOW()
				WHERE domain_uuid = :domain_uuid";
		} else {
			// Inserir
			$sql = "INSERT INTO v_voice_omniplay_settings 
				(omniplay_setting_uuid, domain_uuid, omniplay_api_url, omniplay_api_token, omniplay_company_id, auto_sync_enabled, sync_interval_minutes)
				VALUES 
				(gen_random_uuid(), :domain_uuid, :omniplay_api_url, :omniplay_api_token, :omniplay_company_id, :auto_sync_enabled, :sync_interval_minutes)";
		}
		
		$stmt = $db->prepare($sql);
		$stmt->execute([
			':domain_uuid' => $domain_uuid,
			':omniplay_api_url' => $omniplay_api_url ?: null,
			':omniplay_api_token' => $omniplay_api_token ?: null,
			':omniplay_company_id' => $omniplay_company_id,
			// PostgreSQL boolean: converter PHP bool para string 't' ou 'f'
			':auto_sync_enabled' => $auto_sync_enabled ? 't' : 'f',
			':sync_interval_minutes' => $sync_interval_minutes
		]);
		
		message::add("Configurações salvas com sucesso!", 'positive');
	} catch (PDOException $e) {
		error_log("OmniPlay settings save error: " . $e->getMessage());
		message::add("Erro ao salvar: A tabela de configurações pode não existir. Execute as migrations primeiro.", 'negative');
	}
	
	header("Location: omniplay_settings.php");
	exit;
}

// Ação: Testar conexão
if ($action === 'test') {
	try {
		$client = new OmniPlayAPIClient($domain_uuid, $db);
		
		if (!$client->isConfigured()) {
			message::add("Configure a URL e o Token primeiro.", 'negative');
		} else {
			$result = $client->testConnection();
			
			if ($result) {
				$company_id = $result['companyId'] ?? null;
				
				// ✅ FIX: Auto-preencher company_id após conexão bem-sucedida
				if ($company_id) {
					try {
						$sql = "UPDATE v_voice_omniplay_settings SET omniplay_company_id = :company_id WHERE domain_uuid = :domain_uuid";
						$stmt = $db->prepare($sql);
						$stmt->execute([':company_id' => $company_id, ':domain_uuid' => $domain_uuid]);
					} catch (PDOException $e) {
						error_log("OmniPlay update company_id error: " . $e->getMessage());
					}
				}
				
				message::add("✅ Conexão OK! Empresa ID: " . ($company_id ?? 'N/A') . " (salvo automaticamente)", 'positive');
			} else {
				message::add("❌ Falha na conexão: " . $client->getLastError(), 'negative');
			}
		}
	} catch (Exception $e) {
		message::add("Erro ao testar conexão: " . $e->getMessage(), 'negative');
	}
	
	header("Location: omniplay_settings.php");
	exit;
}

// Ação: Forçar sincronização
if ($action === 'sync') {
	try {
		$client = new OmniPlayAPIClient($domain_uuid, $db);
		
		if (!$client->isConfigured()) {
			message::add("Configure a URL e o Token primeiro.", 'negative');
		} else {
			$data = $client->forceSync();
			
			$queue_count = count($data['queues'] ?? []);
			$user_count = count($data['users'] ?? []);
			
			// ✅ FIX: Verificar se realmente obteve dados ou se houve erro
			if ($queue_count === 0 && $user_count === 0 && $data['company'] === null) {
				// Provavelmente houve erro
				$error = $client->getLastError() ?: 'Nenhum dado retornado';
				try {
					$sql = "UPDATE v_voice_omniplay_settings SET last_sync_error = :error WHERE domain_uuid = :domain_uuid";
					$stmt = $db->prepare($sql);
					$stmt->execute([':error' => $error, ':domain_uuid' => $domain_uuid]);
				} catch (PDOException $e) {
					error_log("OmniPlay sync error update failed: " . $e->getMessage());
				}
				
				message::add("⚠️ Sincronização concluída mas sem dados. Verifique a conexão: {$error}", 'warning');
			} else {
				// Atualizar last_sync_at
				try {
					$sql = "UPDATE v_voice_omniplay_settings SET last_sync_at = NOW(), last_sync_error = NULL WHERE domain_uuid = :domain_uuid";
					$stmt = $db->prepare($sql);
					$stmt->execute([':domain_uuid' => $domain_uuid]);
				} catch (PDOException $e) {
					error_log("OmniPlay sync timestamp update failed: " . $e->getMessage());
				}
				
				message::add("✅ Sincronização concluída! {$queue_count} filas, {$user_count} usuários.", 'positive');
			}
		}
	} catch (Exception $e) {
		message::add("Erro na sincronização: " . $e->getMessage(), 'negative');
	}
	
	header("Location: omniplay_settings.php");
	exit;
}

// Buscar dados para exibição
$client = null;
$queues = [];
$users = [];

try {
	$client = new OmniPlayAPIClient($domain_uuid, $db);
	$queues = $client->isConfigured() ? $client->getQueues() : [];
	$users = $client->isConfigured() ? $client->getUsers() : [];
} catch (Exception $e) {
	error_log("OmniPlay client init for display: " . $e->getMessage());
}

// Recarregar settings após ações
try {
	$stmt = $db->prepare("SELECT * FROM v_voice_omniplay_settings WHERE domain_uuid = :domain_uuid LIMIT 1");
	$stmt->execute([':domain_uuid' => $domain_uuid]);
	$settings = $stmt->fetch(PDO::FETCH_ASSOC) ?: [];
} catch (PDOException $e) {
	// Tabela pode não existir
	$settings = [];
}

// ✅ FIX: $current_page DEVE ser definido ANTES de incluir nav_tabs.php
$current_page = 'omniplay_settings';

// Cabeçalho (nav_tabs.php é incluído dentro de secretary_header ou após header)
require_once "resources/header.php";

// Incluir navegação APÓS o header
require_once "resources/nav_tabs.php";

// Breadcrumb
echo "<b class='heading'><a href='voice_secretaries.php'>".$text['title-voice_secretaries']."</a> » Integração OmniPlay</b><br><br>";

// Mensagens
if (class_exists('message')) {
	echo message::html();
}

// CSRF Token
$token = new token;
$token_data = $token->create($_SERVER['PHP_SELF']);
?>

<style>
.omniplay-card {
	background: #fff;
	border: 1px solid #ddd;
	border-radius: 8px;
	padding: 20px;
	margin-bottom: 20px;
}

.omniplay-card h3 {
	margin-top: 0;
	color: #333;
	border-bottom: 2px solid #4CAF50;
	padding-bottom: 10px;
}

.omniplay-status {
	display: inline-block;
	padding: 5px 15px;
	border-radius: 20px;
	font-weight: bold;
}

.omniplay-status.connected {
	background: #e8f5e9;
	color: #2e7d32;
}

.omniplay-status.disconnected {
	background: #ffebee;
	color: #c62828;
}

.omniplay-data-grid {
	display: grid;
	grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
	gap: 10px;
	max-height: 300px;
	overflow-y: auto;
	padding: 10px;
	background: #f5f5f5;
	border-radius: 4px;
}

.omniplay-data-item {
	background: #fff;
	padding: 10px;
	border-radius: 4px;
	border: 1px solid #ddd;
}

.omniplay-data-item .name {
	font-weight: bold;
	color: #333;
}

.omniplay-data-item .meta {
	font-size: 0.85em;
	color: #666;
}

.action-buttons {
	margin-top: 20px;
}

.action-buttons a, .action-buttons button {
	margin-right: 10px;
}

.btn-test {
	background: #2196F3;
	color: #fff;
	border: none;
	padding: 8px 20px;
	border-radius: 4px;
	cursor: pointer;
	text-decoration: none;
}

.btn-sync {
	background: #4CAF50;
	color: #fff;
	border: none;
	padding: 8px 20px;
	border-radius: 4px;
	cursor: pointer;
	text-decoration: none;
}

.help-text {
	font-size: 0.85em;
	color: #666;
	margin-top: 5px;
}

.info-box {
	background: #e3f2fd;
	border: 1px solid #90caf9;
	border-radius: 4px;
	padding: 15px;
	margin-bottom: 20px;
}

.info-box h4 {
	margin: 0 0 10px 0;
	color: #1565c0;
}
</style>

<div class="info-box">
	<h4>ℹ️ Integração OmniPlay</h4>
	<p>
		Configure a conexão com o OmniPlay para sincronizar automaticamente as filas e usuários.
		Isso permite que os campos de fallback (Fila Destino, Usuário Atribuído) sejam preenchidos
		dinamicamente nas secretárias e regras de transferência.
	</p>
	<p>
		<strong>Como obter o Token:</strong><br>
		1. Acesse o OmniPlay → Configurações → Integrações → Voice AI<br>
		2. Clique em "Gerar Token de API"<br>
		3. Copie o token gerado e cole abaixo
	</p>
</div>

<form method="post" action="">
	<input type="hidden" name="<?php echo $token_data['name']; ?>" value="<?php echo $token_data['hash']; ?>">
	
	<div class="omniplay-card">
		<h3>🔗 Configurações de Conexão</h3>
		
		<table width="100%" border="0" cellpadding="0" cellspacing="0">
			<tr>
				<td width="20%" valign="top" class="vncellreq">URL da API</td>
				<td width="80%" class="vtable">
					<input type="text" class="formfld" name="omniplay_api_url" 
						value="<?php echo htmlspecialchars($settings['omniplay_api_url'] ?? ''); ?>"
						placeholder="https://api.omniplay.com.br" style="width: 400px;">
					<div class="help-text">URL base da API do OmniPlay (sem barra no final)</div>
				</td>
			</tr>
		<tr>
			<td valign="top" class="vncellreq">Token de API</td>
			<td class="vtable">
				<input type="password" class="formfld" name="omniplay_api_token" 
					value="<?php echo htmlspecialchars($settings['omniplay_api_token'] ?? ''); ?>"
					placeholder="voice_xxxxxxxxxxxxxxxx" style="width: 400px;" 
					id="api_token_field" autocomplete="off">
				<button type="button" onclick="toggleTokenVisibility()" class="btn btn-default btn-sm" style="margin-left: 5px;">
					👁️ Ver
				</button>
				<div class="help-text">Token gerado no OmniPlay (começa com "voice_")</div>
				<div class="help-text" style="color: #c62828; margin-top: 5px;">
					⚠️ <strong>Segurança:</strong> Este token é armazenado localmente para fazer chamadas à API. 
					Se precisar revogar acesso, gere um novo token no OmniPlay (o anterior será invalidado).
				</div>
			</td>
		</tr>
			<tr>
				<td valign="top" class="vncell">ID da Empresa</td>
				<td class="vtable">
					<input type="number" class="formfld" name="omniplay_company_id" 
						value="<?php echo htmlspecialchars($settings['omniplay_company_id'] ?? ''); ?>"
						placeholder="Ex: 1" style="width: 100px;">
					<div class="help-text">ID numérico da empresa no OmniPlay (preenchido automaticamente após testar conexão)</div>
				</td>
			</tr>
			<tr>
				<td valign="top" class="vncell">Sincronização Automática</td>
				<td class="vtable">
					<?php $sync_enabled = ($settings['auto_sync_enabled'] ?? '') === 't' || $settings['auto_sync_enabled'] === 'true' || $settings['auto_sync_enabled'] === true; ?>
					<input type="checkbox" name="auto_sync_enabled" value="1" 
						<?php echo ($sync_enabled ? 'checked' : ''); ?>>
					Habilitar sincronização automática
					
					<span style="margin-left: 20px;">
						a cada 
						<input type="number" class="formfld" name="sync_interval_minutes" 
							value="<?php echo htmlspecialchars($settings['sync_interval_minutes'] ?? 5); ?>"
							min="1" max="60" style="width: 60px;">
						minutos
					</span>
				</td>
			</tr>
		</table>
		
		<div class="action-buttons">
			<input type="submit" class="btn" value="Salvar Configurações">
			<a href="omniplay_settings.php?action=test" class="btn-test">🔌 Testar Conexão</a>
			<a href="omniplay_settings.php?action=sync" class="btn-sync">🔄 Forçar Sincronização</a>
		</div>
	</div>
</form>

<!-- Status da Conexão -->
<div class="omniplay-card">
	<h3>📊 Status da Integração</h3>
	
	<?php if ($client && $client->isConfigured()): ?>
		<p>
			<span class="omniplay-status connected">✅ Configurado</span>
			<?php if (!empty($settings['last_sync_at'])): ?>
				<span style="margin-left: 20px;">Última sincronização: <?php echo date('d/m/Y H:i', strtotime($settings['last_sync_at'])); ?></span>
			<?php endif; ?>
		</p>
		
		<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 20px;">
			<!-- Filas -->
			<div>
				<h4>📋 Filas do OmniPlay (<?php echo count($queues); ?>)</h4>
				<?php if (!empty($queues)): ?>
					<div class="omniplay-data-grid">
						<?php foreach ($queues as $queue): ?>
							<div class="omniplay-data-item">
								<div class="name"><?php echo htmlspecialchars($queue['name']); ?></div>
								<div class="meta">ID: <?php echo htmlspecialchars($queue['id']); ?></div>
							</div>
						<?php endforeach; ?>
					</div>
				<?php else: ?>
					<p><em>Nenhuma fila encontrada. Clique em "Forçar Sincronização".</em></p>
				<?php endif; ?>
			</div>
			
			<!-- Usuários -->
			<div>
				<h4>👤 Usuários do OmniPlay (<?php echo count($users); ?>)</h4>
				<?php if (!empty($users)): ?>
					<div class="omniplay-data-grid">
						<?php foreach ($users as $user): ?>
							<div class="omniplay-data-item">
								<div class="name"><?php echo htmlspecialchars($user['name']); ?></div>
								<div class="meta">
									ID: <?php echo htmlspecialchars($user['id']); ?> 
									<?php if (!empty($user['online'])): ?>
										<span style="color: #4CAF50;">● Online</span>
									<?php else: ?>
										<span style="color: #999;">○ Offline</span>
									<?php endif; ?>
								</div>
							</div>
						<?php endforeach; ?>
					</div>
				<?php else: ?>
					<p><em>Nenhum usuário encontrado. Clique em "Forçar Sincronização".</em></p>
				<?php endif; ?>
			</div>
		</div>
		
	<?php else: ?>
		<p>
			<span class="omniplay-status disconnected">❌ Não Configurado</span>
		</p>
		<p>Configure a URL e o Token acima para habilitar a integração.</p>
	<?php endif; ?>
</div>

<script>
function toggleTokenVisibility() {
	var field = document.getElementById('api_token_field');
	if (field.type === 'password') {
		field.type = 'text';
	} else {
		field.type = 'password';
	}
}
</script>

<?php
require_once "resources/footer.php";
?>
