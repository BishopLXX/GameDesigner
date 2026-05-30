param(
    [int]$DesiredAiPseudoPairs = 300,
    [int]$BatchLimit = 50,
    [int]$Workers = 2,
    [int]$RequestTimeout = 300,
    [int]$MaxWidth = 320,
    [int]$MaxHeight = 320,
    [int]$InitialVariants = 2,
    [int]$MaxVariants = 3,
    [int]$MaxNoProgressBatches = 2,
    [string]$ModelId = "pixel-refiner-v4.2-ai-pseudo-crops",
    [int]$Epochs = 4,
    [int]$StepsPerEpoch = 900,
    [int]$BatchSize = 6,
    [int]$Features = 64,
    [int]$Seed = 20240530,
    [double]$LearningRate = 1.0e-5,
    [double]$GradClip = 0.1,
    [bool]$UseAmp = $false,
    [double]$AiPseudoWeight = 48.0,
    [string]$RunRootOverride = "",
    [switch]$RenderContactSheet,
    [switch]$SkipTraining
)

$ErrorActionPreference = "Stop"

$ProjectRoot = "D:\GameDesigner"
$SourceRoot = Join-Path $ProjectRoot "source"
$DataRoot = "D:\GameDesignerData\pixel_refiner"
$DatasetRoot = Join-Path $DataRoot "datasets\gold_pndsndn_v1"
$TargetRoot = Join-Path $DatasetRoot "targets\pndsndn_fc2_single_crops_v1"
$RunRoot = if ([string]::IsNullOrWhiteSpace($RunRootOverride)) {
    Join-Path $DataRoot ("runs\{0}_v42_ai_pseudo_then_train" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
} else {
    $RunRootOverride
}
$ModelDir = Join-Path $DataRoot ("models\{0}" -f $ModelId)
$TrainingPython = "D:\GameDesignerData\venvs\pixel-refiner-train\Scripts\python.exe"
$TrainingMain = Join-Path $SourceRoot "pixel_refiner_training_main.py"

New-Item -ItemType Directory -Force -Path $RunRoot | Out-Null
$env:PYTHONPATH = $SourceRoot

function Write-RunLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    $line | Tee-Object -FilePath (Join-Path $RunRoot "orchestrator.log") -Append
}

function Invoke-PixelCliJson {
    param([string[]]$Arguments)
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & py -3 $TrainingMain @Arguments 2>&1
        $exit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exit -ne 0) {
        throw ($output -join "`n")
    }
    return (($output -join "`n") | ConvertFrom-Json)
}

function Get-AiPseudoCount {
    $summary = Invoke-PixelCliJson @("summary")
    return [int]$summary.input_kinds.ai_pseudo
}

function Save-Summary {
    param([string]$Path)
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $summaryText = & py -3 $TrainingMain summary 2>&1
        $exit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exit -ne 0) {
        throw ($summaryText -join "`n")
    }
    ($summaryText -join "`n") | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Render-AiPseudoContactSheet {
    param(
        [string]$OutputPath,
        [int]$Limit = 48
    )
    $code = @'
import json
import sys
from pathlib import Path
from PIL import Image, ImageDraw

dataset = Path(sys.argv[1])
output = Path(sys.argv[2])
limit = int(sys.argv[3])
index_path = dataset / "index.jsonl"
records = []
with index_path.open("r", encoding="utf-8-sig") as f:
    for line in f:
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("input_kind") == "ai_pseudo":
            records.append(row)
records = records[-limit:]
cell_w, cell_h = 256, 150
cols = 4
rows = max(1, (len(records) + cols - 1) // cols)
sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
draw = ImageDraw.Draw(sheet)
for i, row in enumerate(records):
    x = (i % cols) * cell_w
    y = (i // cols) * cell_h
    input_path = Path(row["input_path"])
    target_path = Path(row["target_path"])
    try:
        with Image.open(input_path) as a, Image.open(target_path) as b:
            ai = a.convert("RGBA")
            tgt = b.convert("RGBA")
            ai.thumbnail((118, 118), Image.Resampling.NEAREST)
            tgt.thumbnail((118, 118), Image.Resampling.NEAREST)
            sheet.paste(Image.new("RGB", ai.size, (240, 240, 240)), (x + 6, y + 22))
            sheet.paste(ai.convert("RGB"), (x + 6, y + 22), ai.getchannel("A"))
            sheet.paste(Image.new("RGB", tgt.size, (240, 240, 240)), (x + 132, y + 22))
            sheet.paste(tgt.convert("RGB"), (x + 132, y + 22), tgt.getchannel("A"))
    except Exception as exc:
        draw.text((x + 6, y + 32), str(exc)[:80], fill=(180, 0, 0))
    draw.text((x + 6, y + 5), "AI pseudo", fill=(30, 30, 30))
    draw.text((x + 132, y + 5), "target", fill=(30, 30, 30))
output.parent.mkdir(parents=True, exist_ok=True)
sheet.save(output)
print(json.dumps({"ok": True, "output": str(output), "records": len(records)}, ensure_ascii=False))
'@
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & py -3 -c $code $DatasetRoot $OutputPath $Limit 2>&1
        $exit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exit -ne 0) {
        throw ($output -join "`n")
    }
    return ($output -join "`n")
}

Write-RunLog "Run root: $RunRoot"
Write-RunLog "Target root: $TargetRoot"
Write-RunLog "Desired ai_pseudo pairs: $DesiredAiPseudoPairs"
Write-RunLog "Initial variants per target: $InitialVariants; max variants: $MaxVariants"

$current = Get-AiPseudoCount
Write-RunLog "Current ai_pseudo pairs: $current"

$batchIndex = 0
$currentVariants = [Math]::Max(1, [int]$InitialVariants)
$maxVariantsEffective = [Math]::Max($currentVariants, [int]$MaxVariants)
$noProgressBatches = 0
while ($current -lt $DesiredAiPseudoPairs) {
    $batchIndex += 1
    $beforeBatch = $current
    $remaining = $DesiredAiPseudoPairs - $current
    $limit = [Math]::Min($BatchLimit, $remaining)
    $batchLog = Join-Path $RunRoot ("generate_batch_{0:000}.log" -f $batchIndex)
    Write-RunLog "Generating batch $batchIndex, limit=$limit, current=$current, variants=$currentVariants"

    $args = @(
        $TrainingMain,
        "generate-ai-pseudo",
        "--target-root", $TargetRoot,
        "--source-id", "pndsndn_fc2_single_crops_v1",
        "--limit", "$limit",
        "--variants", "$currentVariants",
        "--workers", "$Workers",
        "--request-timeout", "$RequestTimeout",
        "--alpha-mode", "soft_target_mask",
        "--background", "auto",
        "--max-width", "$MaxWidth",
        "--max-height", "$MaxHeight"
    )
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & py -3 @args *> $batchLog
        $exit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    Write-RunLog "Batch $batchIndex exited with code $exit"
    if ($exit -ne 0) {
        Write-RunLog "Batch $batchIndex failed. See $batchLog"
        if ($Workers -gt 1) {
            $Workers = 1
            Write-RunLog "Reducing workers to 1 for later retries."
        } else {
            throw "AI pseudo generation failed with workers=1. See $batchLog"
        }
    }

    $current = Get-AiPseudoCount
    $summaryPath = Join-Path $RunRoot ("summary_after_batch_{0:000}.json" -f $batchIndex)
    Save-Summary $summaryPath
    if ($RenderContactSheet) {
        $sheetPath = Join-Path $RunRoot ("contact_sheet_after_batch_{0:000}.png" -f $batchIndex)
        try {
            Render-AiPseudoContactSheet $sheetPath 48 | Tee-Object -FilePath (Join-Path $RunRoot "contact_sheet.log") -Append | Out-Null
            Write-RunLog "Contact sheet: $sheetPath"
        } catch {
            Write-RunLog "Contact sheet skipped after error: $($_.Exception.Message)"
        }
    }
    Write-RunLog "After batch $batchIndex ai_pseudo pairs: $current"
    if ($current -le $beforeBatch) {
        $noProgressBatches += 1
        Write-RunLog "No ai_pseudo progress in batch $batchIndex ($noProgressBatches/$MaxNoProgressBatches)."
        if ($noProgressBatches -ge $MaxNoProgressBatches) {
            if ($currentVariants -lt $maxVariantsEffective) {
                $currentVariants += 1
                $noProgressBatches = 0
                Write-RunLog "Increasing variants per target to $currentVariants to continue data generation."
            } else {
                Write-RunLog "Stopping data generation: no eligible targets remain at variants=$currentVariants."
                break
            }
        }
    } else {
        $noProgressBatches = 0
    }
}

if ($SkipTraining) {
    Write-RunLog "SkipTraining set. Done after data generation."
    exit 0
}

if (!(Test-Path -LiteralPath $TrainingPython)) {
    throw "Training python not found: $TrainingPython"
}

$trainEvents = Join-Path $RunRoot "train_events.jsonl"
$trainStdout = Join-Path $RunRoot "train_stdout.log"
Write-RunLog "Starting training: $ModelId"
Write-RunLog "Model dir: $ModelDir"
Write-RunLog "Training config: epochs=$Epochs steps_per_epoch=$StepsPerEpoch batch_size=$BatchSize learning_rate=$LearningRate features=$Features seed=$Seed use_amp=$UseAmp grad_clip=$GradClip"

$trainArgs = @(
    $TrainingMain,
    "train",
    "--model-id", $ModelId,
    "--output-dir", $ModelDir,
    "--epochs", "$Epochs",
    "--steps-per-epoch", "$StepsPerEpoch",
    "--batch-size", "$BatchSize",
    "--patch-size", "64",
    "--learning-rate", "$LearningRate",
    "--seed", "$Seed",
    "--device", "cuda",
    "--features", "$Features",
    "--internal-scale", "2",
    "--tile-overlap", "16",
    "--ai-pseudo-weight", "$AiPseudoWeight",
    "--software-candidate-weight", "32",
    "--grad-clip", "$GradClip",
    "--event-log", $trainEvents
)
if (-not $UseAmp) {
    $trainArgs += "--no-amp"
}

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $TrainingPython @trainArgs *> $trainStdout
    $trainExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
Write-RunLog "Training exited with code $trainExit"
if ($trainExit -ne 0) {
    throw "Training failed. See $trainStdout"
}

$smokeLog = Join-Path $RunRoot "smoke_model.log"
$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $TrainingPython $TrainingMain smoke-model `
        --model-dir $ModelDir `
        --model-id $ModelId `
        --output-dir (Join-Path $RunRoot "smoke_outputs") `
        *> $smokeLog
    $smokeExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}

if ($smokeExit -ne 0) {
    throw "Smoke test failed. See $smokeLog"
}

Write-RunLog "Smoke test log: $smokeLog"
Write-RunLog "Done."
