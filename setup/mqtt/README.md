# VerneMQ MQTT Setup

Local VerneMQ broker with secure TLS bridge to cloud infrastructure.

## Architecture

```
Devices (on-prem) ──TCP:1883──→ Local VerneMQ ──TLS:443──→ Cloud VerneMQ
                                     │                          │
                              JWT webhook auth           JWT webhook auth
                                     │                          │
                              capture-service            capture-service
```

## Security Notice

> **This repository is public.** Never commit credentials or secrets.

- Bridge credentials use **placeholder values** in `vernemq-local.conf`
- Real credentials are passed via **environment variables** at container runtime
- The `setup_mqtt.sh` script reads config and passes values to Docker

## Quick Start

### 1. Update Configuration

Edit `vernemq-local.conf` with your bridge settings:

```conf
vmq_bridge.ssl.gke = your-mqtt-broker.example.com:443
vmq_bridge.ssl.gke.username = bridge
vmq_bridge.ssl.gke.password = your-bridge-password
```

> **Note:** These values are read by `setup_mqtt.sh` and passed as environment variables to the container.

### 2. Run VerneMQ

```bash
sudo ENV=dev ./setup_mqtt.sh
```

### 3. Verify Bridge Connection

```bash
# Check bridge status
docker exec vernemq /vernemq/bin/vmq-admin bridge show

# Check listeners
docker exec vernemq /vernemq/bin/vmq-admin listener show

# Check sessions (shows connected clients including bridge)
docker exec vernemq /vernemq/bin/vmq-admin session show
```

Expected bridge output:
```
+------+-----------------------------+-------------+
| name | endpoint                    | buffer size |
+------+-----------------------------+-------------+
| gke  | your-broker.example.com:443 | 0           |
+------+-----------------------------+-------------+
```

### 4. Test Local MQTT

```bash
# Using Python
python3 -c "
import paho.mqtt.client as mqtt
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.connect('localhost', 1883)
c.publish('test/hello', 'world')
c.disconnect()
print('OK')
"
```

## Configuration Files

### vernemq-local.conf

Local VerneMQ configuration including:
- **Listener**: TCP port 1883 (local devices)
- **Webhooks**: JWT authentication via capture-service
- **Bridge**: TLS connection to cloud broker on port 443

### setup_mqtt.sh

Container setup script that:
1. Reads bridge settings from `vernemq-local.conf`
2. Passes credentials as Docker environment variables
3. Mounts config file to `/vernemq/etc/conf.d/local.conf`
4. Enables bridge plugin via `DOCKER_VERNEMQ_PLUGINS__VMQ_BRIDGE=on`

## Environment Variables

The setup script sets these Docker environment variables:

| Variable | Description |
|----------|-------------|
| `DOCKER_VERNEMQ_VMQ_BRIDGE__SSL__GKE` | Bridge endpoint (host:port) |
| `DOCKER_VERNEMQ_VMQ_BRIDGE__SSL__GKE__USERNAME` | Bridge username |
| `DOCKER_VERNEMQ_VMQ_BRIDGE__SSL__GKE__PASSWORD` | Bridge password |
| `DOCKER_VERNEMQ_VMQ_BRIDGE__SSL__GKE__CLIENT_ID` | Bridge client ID |
| `DOCKER_VERNEMQ_VMQ_BRIDGE__SSL__GKE__INSECURE` | Skip cert verification (on/off) |
| `DOCKER_VERNEMQ_VMQ_BRIDGE__SSL__GKE__TLS_VERSION` | TLS version (tlsv1.2) |
| `DOCKER_VERNEMQ_PLUGINS__VMQ_BRIDGE` | Enable bridge plugin (on) |

## Troubleshooting

### Bridge not connecting

1. **Check DNS resolution:**
   ```bash
   docker exec vernemq nslookup your-broker.example.com
   ```

2. **Test TLS connectivity:**
   ```bash
   docker exec vernemq sh -c "echo 'Q' | openssl s_client -connect your-broker.example.com:443 -tls1_2"
   ```

3. **Check container logs:**
   ```bash
   docker logs vernemq 2>&1 | tail -50
   ```

4. **Verify bridge plugin is enabled:**
   ```bash
   docker exec vernemq cat /vernemq/etc/vernemq.conf | grep vmq_bridge
   ```

### Webhook authentication failing

Verify capture-service is accessible:

```bash
docker exec vernemq wget -q -O- http://172.17.0.1:5000/health
```

### Container keeps restarting

```bash
# Check logs
docker logs vernemq

# Validate config
docker exec vernemq /vernemq/bin/vernemq config generate -l debug
```

## File Structure

```
setup/mqtt/
├── vernemq-local.conf    # VerneMQ configuration (placeholder credentials)
├── setup_mqtt.sh         # Container setup script
└── README.md             # This file
```

## Security Best Practices

1. **Never commit real credentials** - Use placeholder values in config files
2. **Use strong passwords** - Generate random bridge passwords
3. **Rotate credentials periodically** - Update bridge passwords regularly
4. **Monitor connections** - Check `vmq-admin session show` for unexpected clients
5. **Use TLS** - Bridge uses TLS 1.2 encryption

## Support

For bridge connectivity or authentication issues, contact your infrastructure administrator.
