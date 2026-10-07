# CDGC Snowflake Updater

Reads metadata from Informatica Cloud Data Governance & Catalog (CDGC) and writes it back to Snowflake as native object tags. Automates what would otherwise require manual spreadsheet uploads through the CDGC UI.

## What it writes

- Business terms
- Parent policies
- Classifications
- Data quality scores
- Asset descriptions

Tags are automatically created in Snowflake if they don't already exist (requires `CREATE TAG` privilege).

## Requirements

- Python 3.x
- Snowflake account with `ACCOUNTADMIN` role (or equivalent tag privileges)
- Informatica CDGC account with access to the target catalog resource

```
pip install -r requirements.txt
```

## Setup

**1. Copy the example config and fill in your values:**

```
cp config.ini.example config.ini
```

**2. Run the interactive setup wizard to configure credentials:**

```
python snowflake_updater.py --update-config
```

This will prompt for each setting and encrypt passwords before saving.

## Configuration

Settings live in `config.ini` (never committed — see `.gitignore`).

### `[IDMC]`
| Key | Description |
|-----|-------------|
| `username` | CDGC login username |
| `password` / `encrypted_password` | CDGC password (plain or encrypted) |
| `pod` | Your CDGC pod, e.g. `dmp-us` |
| `catalog_resource_name` | Name of the Snowflake resource in CDGC |

### `[Snowflake]`
| Key | Description |
|-----|-------------|
| `account` | Snowflake account identifier |
| `database` | Target database |
| `username` | Snowflake login username |
| `password` / `encrypted_password` | Snowflake password (plain or encrypted) |
| `role` | Snowflake role to use |
| `private_key_file` | *(Optional)* Path to a `.p8` private key file for key-pair auth |
| `private_key_passphrase` | *(Optional)* Passphrase for the private key (omit if unencrypted) |

If `private_key_file` is set, password fields are ignored and key-pair authentication is used instead.

**Windows path example:**
```ini
private_key_file = C:\Users\YourName\.ssh\snowflake_key.p8
```
**Mac/Linux path example:**
```ini
private_key_file = /Users/yourname/.ssh/snowflake_key.p8
```

### `[TagWriteback]`
Each metadata type can be independently enabled/disabled. Set `writeback_<type> = False` to skip it. The corresponding `*_tag` value must match the fully-qualified Snowflake tag name (`database.schema.tag_name`).

`stop_and_verify = True` pauses after building the statement list so you can review before any changes are written to Snowflake.

## Usage

```
python snowflake_updater.py
```

Or with a custom config file:

```
python snowflake_updater.py --config myconfig.ini
```

All output is saved to a timestamped log file in the script directory: `run_snowflake_updater_YYYYMMDD_HHMMSS.log`
