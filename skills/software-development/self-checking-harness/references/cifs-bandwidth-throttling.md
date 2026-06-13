# CIFS Bandwidth Throttling

When working with large file operations over network mounts (Windows shares via /mnt/), throttle batch I/O to avoid saturating the link. Use `rsync --bwlimit` or rate-limited loops.
