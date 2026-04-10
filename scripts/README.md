# Sauvegarde Neo4j vers OwnCloud

Système de sauvegarde automatique des données Neo4j vers OwnCloud via WebDAV.

## Fichiers

- `backup-neo4j-to-owncloud.sh` - Script principal de sauvegarde
- `backup-config.env` - Configuration des credentials OwnCloud
- `neo4j-backup.service` - Service systemd pour la sauvegarde
- `neo4j-backup.timer` - Timer systemd pour les sauvegardes récurrentes

## Installation

### 1. Configuration

Éditez le fichier `backup-config.env` avec vos credentials OwnCloud :

```bash
# URL OwnCloud WebDAV
OWNCLOUD_URL="https://your-owncloud-instance.com/remote.php/webdav"

# Credentials OwnCloud
OWNCLOUD_USER="your-username"
OWNCLOUD_PASSWORD="your-password"

# Répertoire de sauvegarde sur OwnCloud
OWNCLOUD_BACKUP_DIR="/Neo4j-Backups"
```

### 2. Installation systemd

Copiez les fichiers systemd vers le répertoire système :

```bash
sudo cp neo4j-backup.service /etc/systemd/system/
sudo cp neo4j-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

### 3. Activation du timer

Activez et démarrez le timer pour les sauvegardes automatiques :

```bash
sudo systemctl enable neo4j-backup.timer
sudo systemctl start neo4j-backup.timer
```

## Utilisation manuelle

Pour exécuter une sauvegarde manuelle :

```bash
./backup-neo4j-to-owncloud.sh
```

Ou via systemd :

```bash
sudo systemctl start neo4j-backup.service
```

## Personnalisation

### Fréquence des sauvegardes

Modifiez `neo4j-backup.timer` pour changer la fréquence :

```ini
# Quotidien (par défaut)
OnCalendar=daily

# Hebdomadaire
OnCalendar=weekly

# Toutes les 6 heures
OnCalendar=*:00/6:00
```

### Rétention

Le script conserve actuellement tous les backups. Pour implémenter une rétention automatique, ajoutez à la fin du script :

```bash
# Garder seulement les 7 derniers backups
cd "${BACKUP_DIR}"
ls -t neo4j-backup-*.tar.gz | tail -n +8 | xargs rm -f
```

## Sécurité

- Le fichier `backup-config.env` contient des credentials sensibles
- Assurez-vous que le fichier a les permissions appropriées : `chmod 600 backup-config.env`
- Considérez l'utilisation de variables d'environnement ou de secrets pour les credentials en production

## Logs

Consultez les logs systemd :

```bash
journalctl -u neo4j-backup.service -f
```

## Dépannage

### Erreur "Fichier de configuration non trouvé"
Vérifiez que `backup-config.env` existe dans le même répertoire que le script.

### Erreur WebDAV
Vérifiez que l'URL OwnCloud est correcte et que les credentials sont valides.
Testez manuellement avec curl :

```bash
curl -u "user:password" "https://your-owncloud-instance.com/remote.php/webdav/"
```

### Erreur Neo4j
Vérifiez que le container Neo4j est en cours d'exécution :

```bash
docker ps | grep neo4j
```
