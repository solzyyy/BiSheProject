# Database Reset Script
# Clear database, drop indexes, rebuild indexes, and re-import all data

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Database Reset Tool" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if running from project root
if (-not (Test-Path "src\cli.py")) {
    Write-Host "Error: Please run this script from project root directory!" -ForegroundColor Red
    exit 1
}

# 1. Clear database
Write-Host "1. Clearing database (deleting all nodes and relationships)..." -ForegroundColor Yellow
python -m src.cli clear-db
if (-not $?) {
    Write-Host "Failed to clear database!" -ForegroundColor Red
    exit 1
}
Write-Host "Database cleared" -ForegroundColor Green
Write-Host ""

# 2. Drop all indexes
Write-Host "2. Dropping all indexes and constraints..." -ForegroundColor Yellow
python -m src.cli drop-indexes
if (-not $?) {
    Write-Host "Failed to drop indexes!" -ForegroundColor Red
    exit 1
}
Write-Host "Indexes dropped" -ForegroundColor Green
Write-Host ""

# 3. Rebuild indexes
Write-Host "3. Rebuilding indexes and constraints..." -ForegroundColor Yellow
python -m src.cli init-indexes
if (-not $?) {
    Write-Host "Failed to build indexes!" -ForegroundColor Red
    exit 1
}
Write-Host "Indexes built" -ForegroundColor Green
Write-Host ""

# 4. Import events and relations
Write-Host "4. Importing events and relations..." -ForegroundColor Yellow
python -m src.cli import-neo4j
if (-not $?) {
    Write-Host "Failed to import events and relations!" -ForegroundColor Red
    exit 1
}
Write-Host "Events and relations imported" -ForegroundColor Green
Write-Host ""

# 5. Import entities
Write-Host "5. Importing entities..." -ForegroundColor Yellow
python -m src.cli import-entities
if (-not $?) {
    Write-Host "Failed to import entities!" -ForegroundColor Red
    exit 1
}
Write-Host "Entities imported" -ForegroundColor Green
Write-Host ""

# 6. Import state changes
Write-Host "6. Importing state changes..." -ForegroundColor Yellow

# 6.1 World states
Write-Host "   Importing world states..." -ForegroundColor Cyan
if (Test-Path "out\world_states.json") {
    python -m src.cli import-state-changes --states out/world_states.json
    if (-not $?) {
        Write-Host "   Failed to import world states!" -ForegroundColor Red
        exit 1
    }
    Write-Host "   World states imported" -ForegroundColor Green
} else {
    Write-Host "   File not found: out\world_states.json, skipping" -ForegroundColor Yellow
}

# 6.2 Character states
Write-Host "   Importing character states..." -ForegroundColor Cyan
if (Test-Path "out\character_states.json") {
    python -m src.cli import-state-changes --states out/character_states.json
    if (-not $?) {
        Write-Host "   Failed to import character states!" -ForegroundColor Red
        exit 1
    }
    Write-Host "   Character states imported" -ForegroundColor Green
} else {
    Write-Host "   File not found: out\character_states.json, skipping" -ForegroundColor Yellow
}

# 6.3 Relationship states
Write-Host "   Importing relationship states..." -ForegroundColor Cyan
if (Test-Path "out\relationship_states.json") {
    python -m src.cli import-state-changes --states out/relationship_states.json
    if (-not $?) {
        Write-Host "   Failed to import relationship states!" -ForegroundColor Red
        exit 1
    }
    Write-Host "   Relationship states imported" -ForegroundColor Green
} else {
    Write-Host "   File not found: out\relationship_states.json, skipping" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "Database reset completed!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
