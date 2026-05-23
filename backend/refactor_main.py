import re

with open('backend/main.py', 'r') as f:
    lines = f.readlines()

new_lines = []
in_endpoints = False

for i, line in enumerate(lines):
    if "@app.get(\"/\")" in line:
        in_endpoints = True
        new_lines.append("\nfrom engine import UserManager\n")
        new_lines.append("user_manager = UserManager(db)\n\n")

    if in_endpoints:
        # Patch endpoint signature
        if re.match(r'^async def \w+\(', line) or re.match(r'^def \w+\(', line):
            if "verify_token" not in line and "def root" not in line:
                if line.strip().endswith('):'):
                    line = line.replace('):', ', user: dict = Depends(verify_token)):')
                elif line.strip().endswith(')'):
                    # Multi-line def? Or something else? Usually endpoints here end with ):
                    pass
                
        # Insert engine retrieval right after def
        if (re.match(r'^async def \w+\(', lines[i-1]) or re.match(r'^def \w+\(', lines[i-1])) and "def root" not in lines[i-1]:
            if '"""' not in line:
                new_lines.append(line)
                new_lines.append("    eng = user_manager.get_engine(user['uid'])\n")
                continue
            else:
                pass 
        
        if '"""' in line and (re.match(r'^async def \w+\(', lines[i-2]) or re.match(r'^def \w+\(', lines[i-2]) or re.match(r'^async def \w+\(', lines[i-3])):
             new_lines.append(line)
             new_lines.append("    eng = user_manager.get_engine(user['uid'])\n")
             continue

        # Replacements
        line = re.sub(r'\bconfig\.', 'eng.config.', line)
        line = re.sub(r'\btrade_log\b', 'eng.trade_log', line)
        line = re.sub(r'\blatest_scans\b', 'eng.latest_scans', line)
        line = re.sub(r'\blatest_scans_by_tf\b', 'eng.latest_scans_by_tf', line)
        line = re.sub(r'\bbot_scans\b', 'eng.bot_scans', line)
        line = re.sub(r'\bbroker\.', 'eng.broker.', line)
        line = re.sub(r'\b_pick_scan\b', 'eng._pick_scan', line) # If it exists
        line = re.sub(r'\bbot_running\b', 'eng.bot_running', line)
        line = re.sub(r'\bcloud_restore_log\b', '[]', line)
        
    new_lines.append(line)

with open('backend/main_refactored.py', 'w') as f:
    f.writelines(new_lines)
