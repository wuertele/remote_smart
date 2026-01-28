# Configuration Examples

## Synology DSM (SAT passthrough)
Devices:

/dev/sata1
/dev/sata2
...
/dev/sata12


Command:

sudo smartctl -x -j -d sat {device}


Parser:
- smartctl_json

---

## Linux HBA
Devices:

/dev/sda
/dev/sdb


Command:

sudo smartctl -x -j {device}



---

## Discovery via command
Discovery command:


lsblk -dn -o NAME,TYPE | awk '$2=="disk"{print "/dev/"$1}'

