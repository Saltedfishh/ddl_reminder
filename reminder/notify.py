import base64
import os
import subprocess


def windows_toast(title, body):
    if os.name != 'nt':
        raise RuntimeError('桌面通知只支持 Windows')
    # XML text nodes avoid script/XML injection from course titles.
    import json
    payload = base64.b64encode(json.dumps([title, body], ensure_ascii=False).encode('utf-8')).decode('ascii')
    script = r'''
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$parts = ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__')))
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text></text><text></text></binding></visual><audio src="ms-winsoundevent:Notification.Reminder"/></toast>')
$nodes = $xml.GetElementsByTagName('text')
$nodes.Item(0).AppendChild($xml.CreateTextNode($parts[0])) | Out-Null
$nodes.Item(1).AppendChild($xml.CreateTextNode($parts[1])) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Microsoft.Windows.PowerShell').Show($toast)
'''.replace('__PAYLOAD__', payload)
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=20)
    if result.returncode:
        # Unpackaged PowerShell hosts may lack an AppUserModelID. A tray balloon
        # works without installing shortcuts or changing the registry.
        fallback = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$parts = ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__')))
$icon = New-Object System.Windows.Forms.NotifyIcon
try {
    $icon.Icon = [System.Drawing.SystemIcons]::Information
    $icon.Text = 'Canvas DDL'
    $icon.Visible = $true
    $icon.ShowBalloonTip(8000, $parts[0], $parts[1], [System.Windows.Forms.ToolTipIcon]::Info)
    $end = [DateTime]::UtcNow.AddSeconds(9)
    while ([DateTime]::UtcNow -lt $end) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 100
    }
} finally { $icon.Dispose() }
'''.replace('__PAYLOAD__', payload)
        encoded = base64.b64encode(fallback.encode('utf-16le')).decode('ascii')
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-STA', '-EncodedCommand', encoded],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=20)
        if result.returncode:
            raise RuntimeError('Windows 未接受通知，请检查通知权限；可在面板通知记录中查看提醒。')


def check_reminders(store, now, notifier=windows_toast):
    for task, stage, left in list(store.reminder_candidates(now)):
        if left <= 0:
            when = '已到截止时间（或已逾期）'
        else:
            hours = left / 3600
            when = f'距离截止约 {hours:.1f} 小时'
        title = 'Canvas 作业提醒'
        body = f"{task['course']} · {task['title']}\n{when}"
        store.notice(title, body)
        try:
            notifier(title, body)
            store.set('notification_error', None)
        except Exception as exc:
            store.set('notification_error', str(exc))
        # In-app inbox is durable delivery even if Windows suppresses toasts.
        store.delivered(task, stage)
