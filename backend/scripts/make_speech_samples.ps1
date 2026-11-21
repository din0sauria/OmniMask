param([string]$OutputDir = (Join-Path $PSScriptRoot '../../.validation/samples'))
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$null = New-Item -ItemType Directory -Force -Path $OutputDir
$OutputDir = (Resolve-Path -LiteralPath $OutputDir).Path
$samples = @(
    @{ File='speech_zh.wav'; Voice='Microsoft Huihui Desktop'; Text='请记录测试号码：一三八，零零一三，八零零零。这个号码仅用于系统测试。' },
    @{ File='speech_en.wav'; Voice='Microsoft Zira Desktop'; Text='This is an English compatibility test. Please review the recording before sharing it.' },
    @{ File='speech_zh_digits.wav'; Voice='Microsoft Huihui Desktop'; Rate=-1; Text='测试联系电话是，幺三九，幺二三四，五六七八。请检查这个测试号码。' }
)
foreach ($sample in $samples) {
    $speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
        $speaker.SelectVoice($sample.Voice)
        if ($sample.ContainsKey('Rate')) { $speaker.Rate = $sample.Rate }
        $speaker.SetOutputToWaveFile((Join-Path $OutputDir $sample.File))
        $speaker.Speak($sample.Text)
    } finally { $speaker.Dispose() }
}
$samples | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $OutputDir 'speech-ground-truth.json') -Encoding utf8
Write-Output $OutputDir
