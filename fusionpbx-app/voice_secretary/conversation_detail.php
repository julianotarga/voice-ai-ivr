<?php
/**
 * Voice Secretary - Conversation Detail Page
 * 
 * Shows full transcript of a conversation.
 * ⚠️ MULTI-TENANT: Uses domain_uuid from session.
 *
 * @package voice_secretary
 */

//includes files
	require_once dirname(__DIR__, 2) . "/resources/require.php";
	require_once "resources/check_auth.php";

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

//get domain_uuid from session
	$domain_uuid = $_SESSION['domain_uuid'] ?? null;
	if (!$domain_uuid) {
		echo "Error: domain_uuid not found in session.";
		exit;
	}

	$database = new database;

// Get conversation
if (!isset($_GET['id']) || empty($_GET['id'])) {
    header('Location: conversations.php');
    exit;
}

$conversation_uuid = $_GET['id'];

$sql = "SELECT c.*, s.secretary_name, s.company_name
        FROM v_voice_conversations c
        LEFT JOIN v_voice_secretaries s ON s.voice_secretary_uuid = c.voice_secretary_uuid
        WHERE c.conversation_uuid = :uuid AND c.domain_uuid = :domain_uuid";
$params = ['uuid' => $conversation_uuid, 'domain_uuid' => $domain_uuid];
$rows = $database->select($sql, $params);

if (!$rows) {
    $_SESSION['message'] = $text['message-conversation_not_found'];
    header('Location: conversations.php');
    exit;
}

$conversation = $rows[0];

// Get messages
$sql_msg = "SELECT * FROM v_voice_messages 
            WHERE conversation_uuid = :uuid AND domain_uuid = :domain_uuid 
            ORDER BY sequence_number ASC";
$messages = $database->select($sql_msg, $params);

// Include header
$document['title'] = $text['title-conversation_detail'];
require_once "resources/header.php";
?>

<div class="action_bar" id="action_bar">
    <div class="heading">
        <b><?php echo $text['title-conversation_detail']; ?></b>
    </div>
    <div class="actions">
        <button type="button" onclick="window.location='conversations.php'" class="btn btn-default btn-sm">
            <span class="fas fa-arrow-left fa-fw"></span>
            <?php echo $text['button-back']; ?>
        </button>
    </div>
    <div style="clear: both;"></div>
</div>

<!-- Conversation Info -->
<div class="card" style="margin-bottom: 20px; padding: 20px; background: #f9f9f9; border-radius: 8px;">
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px;">
        <div>
            <strong><?php echo $text['label-date']; ?>:</strong><br>
            <?php echo date('d/m/Y H:i:s', strtotime($conversation['created_at'])); ?>
        </div>
        <div>
            <strong><?php echo $text['label-caller_id']; ?>:</strong><br>
            <?php echo escape($conversation['caller_id']); ?>
        </div>
        <div>
            <strong><?php echo $text['label-secretary']; ?>:</strong><br>
            <?php echo escape($conversation['secretary_name'] ?? '—'); ?>
        </div>
        <div>
            <strong><?php echo $text['label-duration']; ?>:</strong><br>
            <?php 
            $mins = floor($conversation['duration_seconds'] / 60);
            $secs = $conversation['duration_seconds'] % 60;
            echo sprintf('%d min %d seg', $mins, $secs);
            ?>
        </div>
        <div>
            <strong><?php echo $text['label-action']; ?>:</strong><br>
            <?php if ($conversation['final_action'] === 'transfer') { ?>
                <span class="badge badge-info">Transferido para <?php echo escape($conversation['transfer_target']); ?></span>
            <?php } elseif ($conversation['final_action'] === 'hangup') { ?>
                <span class="badge badge-success">Resolvido</span>
            <?php } else { ?>
                <span class="badge badge-warning"><?php echo escape($conversation['final_action']); ?></span>
            <?php } ?>
        </div>
    </div>
</div>

<!-- Transcript -->
<div class="transcript" style="max-width: 800px;">
    <h4 style="margin-bottom: 15px;">
        <i class="fas fa-comments"></i> <?php echo $text['label-transcript']; ?>
    </h4>
    
    <?php foreach ($messages as $msg) { ?>
        <div class="message <?php echo ($msg['role'] === 'user') ? 'message-user' : 'message-assistant'; ?>" 
             style="padding: 15px; margin-bottom: 10px; border-radius: 10px; 
                    <?php echo ($msg['role'] === 'user') 
                        ? 'background: #e3f2fd; margin-left: 50px;' 
                        : 'background: #f5f5f5; margin-right: 50px;'; ?>">
            <div style="font-size: 12px; color: #666; margin-bottom: 5px;">
                <?php if ($msg['role'] === 'user') { ?>
                    <i class="fas fa-user"></i> <?php echo $text['label-caller']; ?>
                <?php } else { ?>
                    <i class="fas fa-robot"></i> <?php echo $text['label-ai']; ?>
                <?php } ?>
                — <?php echo date('H:i:s', strtotime($msg['created_at'])); ?>
            </div>
            <div><?php echo nl2br(escape($msg['content'])); ?></div>
            
            <?php if ($msg['audio_file']) { ?>
                <div style="margin-top: 10px;">
                    <audio controls style="height: 30px;">
                        <source src="<?php echo escape($msg['audio_file']); ?>" type="audio/wav">
                    </audio>
                </div>
            <?php } ?>
            
            <?php if ($msg['detected_intent']) { ?>
                <div style="margin-top: 5px; font-size: 11px; color: #888;">
                    <i class="fas fa-tag"></i> Intent: <?php echo escape($msg['detected_intent']); ?>
                    (<?php echo number_format($msg['intent_confidence'] * 100, 1); ?>%)
                </div>
            <?php } ?>
        </div>
    <?php } ?>
    
    <?php if (empty($messages)) { ?>
        <p class="text-muted"><?php echo $text['message-no_messages']; ?></p>
    <?php } ?>
</div>

<?php
// Include footer
require_once "resources/footer.php";
?>
