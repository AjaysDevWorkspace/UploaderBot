#!/bin/bash
# Newton MCQ Bot - menu for macOS (also works on Linux).
# Run from Terminal:  ./run.sh   (or:  bash run.sh)

cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
VPY=".venv/bin/python"

if [ ! -x "$VPY" ]; then
  echo "Setup has not been run yet. Run ./setup.sh first."
  exit 1
fi

pause() {
  echo
  read -r -p "Press Enter to continue..." _ || exit 0
}

pick_file() {
  while true; do
    echo
    echo "============================================"
    echo "  Newton MCQ Bot"
    echo "============================================"
    echo "Word files in the input folder:"
    echo
    shopt -s nullglob nocaseglob
    files=(input/*.docx)
    shopt -u nullglob nocaseglob
    if [ ${#files[@]} -eq 0 ]; then
      echo "  none found - put your .docx file in the input folder"
    else
      i=1
      for f in "${files[@]}"; do
        echo "  $i  ${f#input/}"
        i=$((i + 1))
      done
    fi
    echo
    read -r -p "Type the file name or its number: " FILE || exit 0
    [ -z "$FILE" ] && continue
    if [[ "$FILE" =~ ^[0-9]+$ ]] && [ "$FILE" -ge 1 ] && [ "$FILE" -le ${#files[@]} ]; then
      FILE="${files[$((FILE - 1))]#input/}"
    fi
    shopt -s nocasematch
    [[ "$FILE" == *.docx ]] || FILE="$FILE.docx"
    shopt -u nocasematch
    if [ -f "input/$FILE" ]; then
      return
    fi
    echo "File not found: input/$FILE"
  done
}

pick_file
while true; do
  echo
  echo "--------------------------------------------"
  echo "File: input/$FILE"
  echo "--------------------------------------------"
  echo "  1  Check the file              - no browser, nothing uploaded"
  echo "  2  Preview one question        - fills the form, does NOT save"
  echo "  3  Upload ONE question         - test upload"
  echo "  4  Upload ALL questions"
  echo "  5  Upload ALL valid questions  - skip invalid ones"
  echo "  6  Rebuild the Excel report"
  echo "  7  Open the output folder"
  echo "  8  Choose another file"
  echo "  0  Exit"
  echo
  read -r -p "Choose a number: " CH || exit 0
  case "$CH" in
    1) "$VPY" main.py check "input/$FILE"; pause ;;
    2) "$VPY" main.py upload "input/$FILE"; pause ;;
    3) "$VPY" main.py upload "input/$FILE" --save --limit 1; pause ;;
    4) "$VPY" main.py upload "input/$FILE" --save; pause ;;
    5) "$VPY" main.py upload "input/$FILE" --save --skip-invalid; pause ;;
    6) "$VPY" main.py report; pause ;;
    7) mkdir -p output
       if command -v open >/dev/null 2>&1; then open output
       elif command -v xdg-open >/dev/null 2>&1; then xdg-open output >/dev/null 2>&1
       else echo "Output folder: $(pwd)/output"; fi ;;
    8) pick_file ;;
    0|q|Q) exit 0 ;;
    *) echo "Please type a number from the list." ;;
  esac
done