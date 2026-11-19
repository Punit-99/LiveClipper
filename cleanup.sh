#!/usr/bin/env bash
# ==============================================================================
# LiveClipper Local Development Clutter Cleanup Script
#
# Cleans up local virtual environments, dependency caches, build outputs, and
# temporary files since all services run in Docker containers.
#
# Preserves:
#   - All source code (backend/app/, frontend/src/)
#   - Configuration files (backend/.env, backend/.env.example)
#   - Docker files (Dockerfile, docker-compose.yml, nginx.conf)
#   - Authentication tokens (backend/.youtube_tokens.json, backend/.tokens.json)
#   - Media outputs (backend/clips/, backend/shorts/, manifest.jsonl)
#   - Log files (backend/highlights.jsonl)
#   - Requirements (requirements.txt, package.json, package-lock.json)
#   - Font files & documentation (backend/app/video/fonts/, README.md)
#
# Usage:
#   ./cleanup.sh [--dry-run] [--keep-buffer]
# ==============================================================================

set -u

# Resolve project root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

DRY_RUN=false
KEEP_BUFFER=false

# Parse command line flags
for arg in "$@"; do
    case "$arg" in
        --dry-run|-n)
            DRY_RUN=true
            ;;
        --keep-buffer)
            KEEP_BUFFER=true
            ;;
        -h|--help)
            echo "Usage: ./cleanup.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --dry-run, -n    Preview files and folders that will be deleted without removing them"
            echo "  --keep-buffer    Preserve the recording buffer directory (.buffer/)"
            echo "  -h, --help       Show this help message and exit"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg"
            echo "Run ./cleanup.sh --help for available options."
            exit 1
            ;;
    esac
done

# Colors for terminal output
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_RESET="\033[0m"
    C_BOLD="\033[1m"
    C_GREEN="\033[32m"
    C_YELLOW="\033[33m"
    C_CYAN="\033[36m"
    C_RED="\033[31m"
    C_GRAY="\033[90m"
else
    C_RESET=""
    C_BOLD=""
    C_GREEN=""
    C_YELLOW=""
    C_CYAN=""
    C_RED=""
    C_GRAY=""
fi

echo -e "${C_BOLD}${C_CYAN}======================================================================${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}                LiveClipper Local Clutter Cleanup                     ${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}======================================================================${C_RESET}"

if [ "$DRY_RUN" = true ]; then
    echo -e "${C_YELLOW}${C_BOLD}[MODE: DRY RUN] No files or directories will be removed.${C_RESET}\n"
else
    echo -e "${C_GREEN}${C_BOLD}[MODE: EXECUTION] Removing clutter files and directories...${C_RESET}\n"
fi

TOTAL_BYTES=0
TOTAL_ITEMS=0

# Helper to format bytes into human-readable representation
format_bytes() {
    local b=$1
    if [ "$b" -ge 1073741824 ]; then
        awk "BEGIN {printf \"%.2f GB\", $b/1073741824}"
    elif [ "$b" -ge 1048576 ]; then
        awk "BEGIN {printf \"%.2f MB\", $b/1048576}"
    elif [ "$b" -ge 1024 ]; then
        awk "BEGIN {printf \"%.2f KB\", $b/1024}"
    else
        printf "%d B" "$b"
    fi
}

# Helper to compute size of a file or directory in bytes
get_size_bytes() {
    local target="$1"
    if [ -L "$target" ]; then
        echo 0
        return
    fi
    if [ -f "$target" ]; then
        # macOS / Linux stat
        if stat -f %z "$target" 2>/dev/null; then
            return
        elif stat -c %s "$target" 2>/dev/null; then
            return
        fi
    fi
    if [ -d "$target" ]; then
        # Use du -sk for portable directory sizing in kilobytes
        local kb
        kb=$(du -sk "$target" 2>/dev/null | cut -f1)
        if [ -n "$kb" ] && [ "$kb" -ge 0 ] 2>/dev/null; then
            echo $((kb * 1024))
            return
        fi
    fi
    echo 0
}

# Process an item for deletion / dry-run reporting
process_item() {
    local category="$1"
    local path="$2"

    if [ ! -e "$path" ] && [ ! -L "$path" ]; then
        return
    fi

    local size
    size=$(get_size_bytes "$path")
    local size_str
    size_str=$(format_bytes "$size")

    local rel_path="${path#$SCRIPT_DIR/}"
    [ "$rel_path" = "$path" ] && rel_path="./$(basename "$path")"

    TOTAL_BYTES=$((TOTAL_BYTES + size))
    TOTAL_ITEMS=$((TOTAL_ITEMS + 1))

    if [ "$DRY_RUN" = true ]; then
        printf "  ${C_GRAY}%-24s${C_RESET} | ${C_CYAN}%10s${C_RESET} | %s\n" "[$category]" "$size_str" "$rel_path"
    else
        printf "  ${C_RED}Deleted${C_RESET} %-17s | ${C_CYAN}%10s${C_RESET} | %s\n" "[$category]" "$size_str" "$rel_path"
        if [ -d "$path" ] && [ ! -L "$path" ]; then
            rm -rf "$path"
        else
            rm -f "$path"
        fi
    fi
}

printf "${C_BOLD}%-26s | %10s | %s${C_RESET}\n" "CATEGORY" "SIZE" "PATH"
echo "----------------------------------------------------------------------"

# 1. Virtual environments
process_item "Virtualenv" "$SCRIPT_DIR/.venv"
process_item "Virtualenv" "$SCRIPT_DIR/venv"
process_item "Virtualenv" "$SCRIPT_DIR/backend/.venv"
process_item "Virtualenv" "$SCRIPT_DIR/backend/venv"

# 2. Node modules & Frontend build
process_item "Frontend Modules" "$SCRIPT_DIR/frontend/node_modules"
process_item "Frontend Build" "$SCRIPT_DIR/frontend/dist"

# 3. Recording Buffer
if [ "$KEEP_BUFFER" = true ]; then
    echo -e "  ${C_YELLOW}Preserved${C_RESET} [Recording Buffer]    |   (skipped)  | backend/.buffer (--keep-buffer)"
else
    process_item "Recording Buffer" "$SCRIPT_DIR/backend/.buffer"
    process_item "Recording Buffer" "$SCRIPT_DIR/.buffer"
fi

# 4. Scratch & test artifacts
process_item "Test Artifact" "$SCRIPT_DIR/backend/test_captions.ass"
process_item "Scratch Directory" "$SCRIPT_DIR/backend/scratch"

# 5. Python cache directories (__pycache__)
# Collect pycache directories excluding venvs and node_modules
while IFS= read -r dir; do
    [ -n "$dir" ] && process_item "Python Cache" "$dir"
done < <(find "$SCRIPT_DIR" -type d -name "__pycache__" \
    -not -path "*/.venv/*" \
    -not -path "*/venv/*" \
    -not -path "*/node_modules/*" 2>/dev/null)

# 6. Test framework caches (.pytest_cache, .mypy_cache)
while IFS= read -r dir; do
    [ -n "$dir" ] && process_item "Test Cache" "$dir"
done < <(find "$SCRIPT_DIR" -type d \( -name ".pytest_cache" -o -name ".mypy_cache" \) \
    -not -path "*/.venv/*" \
    -not -path "*/venv/*" \
    -not -path "*/node_modules/*" 2>/dev/null)

# 7. OS metadata files (.DS_Store)
while IFS= read -r file; do
    [ -n "$file" ] && process_item "OS Metadata" "$file"
done < <(find "$SCRIPT_DIR" -type f -name ".DS_Store" 2>/dev/null)

# 8. Standalone compiled python files (*.pyc, *.pyo)
while IFS= read -r file; do
    [ -n "$file" ] && process_item "Bytecode File" "$file"
done < <(find "$SCRIPT_DIR" -type f \( -name "*.pyc" -o -name "*.pyo" \) \
    -not -path "*/.venv/*" \
    -not -path "*/venv/*" \
    -not -path "*/node_modules/*" \
    -not -path "*/__pycache__/*" 2>/dev/null)

# 9. Standalone temp files (*.tmp)
while IFS= read -r file; do
    [ -n "$file" ] && process_item "Temporary File" "$file"
done < <(find "$SCRIPT_DIR" -type f -name "*.tmp" \
    -not -path "*/.venv/*" \
    -not -path "*/venv/*" \
    -not -path "*/node_modules/*" 2>/dev/null)

echo "----------------------------------------------------------------------"
FORMATTED_TOTAL=$(format_bytes "$TOTAL_BYTES")

if [ "$DRY_RUN" = true ]; then
    echo -e "${C_BOLD}${C_GREEN}DRY RUN SUMMARY:${C_RESET} $TOTAL_ITEMS item(s) would be deleted."
    echo -e "${C_BOLD}${C_GREEN}TOTAL DISK SPACE TO FREE:${C_RESET} ${C_BOLD}${C_CYAN}$FORMATTED_TOTAL${C_RESET}"
    echo -e "\n${C_GRAY}Run ./cleanup.sh without --dry-run to permanently delete these items.${C_RESET}"
else
    echo -e "${C_BOLD}${C_GREEN}CLEANUP COMPLETE:${C_RESET} Successfully removed $TOTAL_ITEMS item(s)."
    echo -e "${C_BOLD}${C_GREEN}TOTAL DISK SPACE FREED:${C_RESET} ${C_BOLD}${C_CYAN}$FORMATTED_TOTAL${C_RESET}"
fi
