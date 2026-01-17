<?php
/*
	FusionPBX
	Version: MPL 1.1

	Voice Secretary - Navigation Tabs
	Include this after require_once "resources/header.php";
	Set $current_page before including.
*/

//check if $current_page is set
	if (!isset($current_page)) {
		$current_page = 'secretaries';
	}

//define navigation items
	$nav_items = [
		'secretaries' => [
			'title' => $text['label-secretaries'] ?? 'Secretaries',
			'path' => 'secretary.php',
			'permission' => 'voice_secretary_view'
		],
		'transfer_destinations' => [
			'title' => $text['label-transfer_destinations'] ?? '📞 Handoff',
			'path' => 'transfer_destinations.php',
			'permission' => 'voice_secretary_view'
		],
		'transfer_rules' => [
			'title' => $text['label-transfer_rules'] ?? 'Regras',
			'path' => 'transfer_rules.php',
			'permission' => 'voice_secretary_view'
		],
		'providers' => [
			'title' => $text['label-ai_providers'] ?? 'AI Providers',
			'path' => 'providers.php',
			'permission' => 'voice_secretary_view'
		],
		'documents' => [
			'title' => $text['label-documents'] ?? 'Documents',
			'path' => 'documents.php',
			'permission' => 'voice_secretary_view'
		],
		'conversations' => [
			'title' => $text['label-conversations'] ?? 'Conversations',
			'path' => 'conversations.php',
			'permission' => 'voice_secretary_view'
		],
		'settings' => [
			'title' => $text['label-settings'] ?? 'Settings',
			'path' => 'settings.php',
			'permission' => 'voice_secretary_edit'
		],
		'omniplay_settings' => [
			'title' => $text['label-omniplay'] ?? '🔗 OmniPlay',
			'path' => 'omniplay_settings.php',
			'permission' => 'voice_secretary_edit'
		],
	];

//count visible tabs
	$visible_tabs = 0;
	foreach ($nav_items as $item) {
		if (permission_exists($item['permission'])) {
			$visible_tabs++;
		}
	}

//only show tabs if more than one is visible
	if ($visible_tabs > 1) {
		//inline styles for tabs
		$tab_style = "
			display: inline-block;
			padding: 10px 20px;
			margin-right: 5px;
			text-decoration: none;
			color: #444;
			background: #f5f5f5;
			border: 1px solid #ddd;
			border-bottom: none;
			border-radius: 5px 5px 0 0;
			font-size: 13px;
			transition: background 0.2s;
		";
		$tab_active_style = "
			display: inline-block;
			padding: 10px 20px;
			margin-right: 5px;
			text-decoration: none;
			color: #fff;
			background: #1e88e5;
			border: 1px solid #1e88e5;
			border-bottom: none;
			border-radius: 5px 5px 0 0;
			font-size: 13px;
			font-weight: bold;
		";
		$container_style = "
			margin-bottom: 20px;
			border-bottom: 2px solid #1e88e5;
			padding-bottom: 0;
		";

		echo "<div style='".$container_style."'>\n";
		foreach ($nav_items as $key => $item) {
			if (permission_exists($item['permission'])) {
				$style = ($current_page === $key) ? $tab_active_style : $tab_style;
				echo "	<a href='".$item['path']."' style='".$style."'>".$item['title']."</a>\n";
			}
		}
		echo "</div>\n";
	}

?>
