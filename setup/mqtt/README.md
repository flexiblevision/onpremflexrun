# VerneMQ MQTT Setup

This directory contains the setup for VerneMQ MQTT broker with secure bridging between on-premises and GKE.

## Architecture

```
Devices (on-prem) ──TCP:1883──→ Local VerneMQ ──TLS:8883──→ GKE VerneMQ
                                     │                           │
                              JWT webhook auth            JWT webhook auth
                                     │                           │
                              capture-service             capture-service
```

## Prerequisites

- Docker installed locally
- `kubectl` configured for your GKE cluster
- Access to `clouddeploy` namespace

## Dev Environment Setup

### Step 1: Generate Certificates

```bash
cd gke
./generate-certs.sh dev
```

This generates all certificates and automatically copies bridge certs to `ssl-dev/`.

### Step 2: Build Local VerneMQ Image

```bash
BRIDGE_ADDRESS=mqtt-dev.flexiblevision.com:8883 \
BRIDGE_PASSWORD=your-bridge-secret \
ENV=dev \
sudo -E ./build.sh
```

Note: `BRIDGE_ADDRESS` and `BRIDGE_PASSWORD` are required. Use `-E` to preserve environment variables with sudo.

### Step 3: Run and Test Local VerneMQ

```bash
sudo ENV=dev ./setup_mqtt.sh
```

Test local MQTT (bridge will fail but local works):

```bash
python3 -c "
import paho.mqtt.client as mqtt
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.connect('localhost', 1883)
c.publish('test/hello', 'world')
c.disconnect()
print('Local MQTT OK')
"
```

### Step 4: Deploy to GKE

```bash
cd gke
ENV=dev ./deploy.sh
```

Note the LoadBalancer IP from the output.

### Step 5: Add DNS Record

```
mqtt-dev.flexiblevision.com → <LoadBalancer-IP>
```

### Step 6: Rebuild and Restart with Bridge Config

Once DNS is configured, rebuild with your bridge credentials:

```bash
BRIDGE_ADDRESS=mqtt-dev.flexiblevision.com:8883 \
BRIDGE_PASSWORD=your-bridge-secret \
ENV=dev \
sudo -E ./build.sh

sudo ENV=dev ./setup_mqtt.sh
```

### Step 7: Verify Bridge

```bash
# Check local broker
docker logs vernemq

# Test MQTT locally
python3 -c "
import paho.mqtt.client as mqtt
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.connect('localhost', 1883)
c.publish('test/hello', 'world')
c.disconnect()
print('OK')
"
```

## Production Environment Setup

```bash
# Generate prod certs (auto-copies to ssl-prod/)
cd gke
./generate-certs.sh prod mqtt.flexiblevision.com

# Deploy to GKE
ENV=prod ./deploy.sh

# Build and run local VerneMQ with prod bridge config
cd ..
BRIDGE_ADDRESS=mqtt.flexiblevision.com:8883 \
BRIDGE_PASSWORD=your-prod-secret \
ENV=prod \
sudo -E ./build.sh

sudo ENV=prod ./setup_mqtt.sh
```

## File Structure

```
setup/mqtt/
├── ssl-dev/                 # Dev bridge certs (local)
├── ssl-prod/                # Prod bridge certs (local)
├── gke/
│   ├── certs-dev/           # Dev CA + server + bridge certs
│   ├── certs-prod/          # Prod CA + server + bridge certs
│   ├── deployment.yaml      # Kubernetes manifests
│   ├── deploy.sh            # GKE deployment script
│   └── generate-certs.sh    # Certificate generation
├── vernemq.conf             # VerneMQ configuration
├── Dockerfile               # VerneMQ container build
├── build.sh                 # Docker build script
├── setup_mqtt.sh            # Local container setup
└── README.md                # This file
```

## Security

- **Device isolation**: Devices connect only to local broker, never directly to cloud
- **TLS encryption**: Bridge uses TLS 1.2+ with client certificates
- **Separate CAs**: Dev and prod use isolated certificate authorities
- **JWT authentication**: Webhook validates tokens on both sides
- **Topic authorization**: Webhooks check claims for write privileges

## Scripts Reference

| Script | Description |
|--------|-------------|
| `BRIDGE_ADDRESS=... BRIDGE_PASSWORD=... ENV=dev sudo -E ./build.sh` | Build VerneMQ Docker image |
| `sudo ENV=dev ./setup_mqtt.sh` | Run local VerneMQ for dev |
| `sudo ENV=prod ./setup_mqtt.sh` | Run local VerneMQ for prod |
| `./gke/generate-certs.sh dev` | Generate dev certificates |
| `./gke/generate-certs.sh prod` | Generate prod certificates |
| `ENV=dev ./gke/deploy.sh` | Deploy to GKE (dev) |
| `ENV=prod ./gke/deploy.sh` | Deploy to GKE (prod) |

## Troubleshooting

### Container keeps restarting

```bash
docker logs vernemq
```

Check for config errors. Run config validation:

```bash
docker run --rm fvonprem/x86-vernemq:latest /vernemq/bin/vernemq config generate -l debug
```

### Bridge not connecting

1. Verify certs are mounted: `docker exec vernemq ls -la /vernemq/etc/ssl/`
2. Check GKE service is accessible: `curl -v telnet://mqtt-dev.flexiblevision.com:8883`
3. Verify DNS resolves correctly

### Webhook auth failing

Check capture-service is running and accessible from the container:

```bash
docker exec vernemq wget -q -O- http://172.17.0.1:5000/health
```
