# GOD INDICATOR Cloud — Resume Guide

## When you come back, just do this:

1. Go to Render: https://dashboard.render.com/web/srv-d9bm3ij7uimc73aph50g
2. Click **Manual Deploy** → Deploy latest commit
3. Open: https://god-indicator-v3.onrender.com/
4. Should show prices within 1 minute ✅

## Current State (Commit 4851cb5)
- ✅ All 13 pairs fetch data correctly (tested locally)
- ✅ calculate_signals() bug fixed
- ✅ socket.setdefaulttimeout(20) prevents any hanging
- ✅ Sequential scanning (no threading issues)

## Diagnostic URLs
- /test-fetch → Test one pair
- /test-all → Test all 13 pairs (~30 seconds)
- /api/status → Engine JSON state

## Still Pending
- [ ] Verify cloud engine works after deploy
- [ ] iPhone Shortcut for ntfy alert → 10s alarm
- [ ] Per-pair toggle switches on dashboard UI
- [ ] Pair neutralization feature
