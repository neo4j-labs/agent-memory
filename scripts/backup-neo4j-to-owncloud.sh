#!/bin/bash
# Script de sauvegarde Neo4j vers OwnCloud via WebDAV
# Auteur: Backup System
# Date: 2026-04-10

# Charger la configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/backup-config.env"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "Erreur: Fichier de configuration non trouvé: $CONFIG_FILE"
    exit 1
fi

source "$CONFIG_FILE"

# Variables
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="neo4j-backup-${TIMESTAMP}.tar.gz"

# Créer le répertoire de sauvegarde
mkdir -p "${BACKUP_DIR}"

# Nettoyer le répertoire de backup avant de créer le nouveau dump
rm -rf "${BACKUP_DIR}/backup"

# Arrêter Neo4j pour permettre un dump cohérent
echo "Arrêt de Neo4j..."
docker stop "${NEO4J_CONTAINER}"

# Attendre que le container soit arrêté
echo "Attente de l'arrêt complet..."
sleep 5

# Faire le dump Neo4j avec un container temporaire utilisant le même volume
echo "Création du dump Neo4j avec container temporaire..."
docker run --rm \
    -v agent-memory_neo4j_test_data:/data \
    -v "${BACKUP_DIR}:/backup" \
    --entrypoint /var/lib/neo4j/bin/neo4j-admin \
    neo4j:5.26-community \
    database dump neo4j --to-path=/backup --overwrite-destination

# Vérifier que le dump a été créé
if [ ! -f "${BACKUP_DIR}/neo4j.dump" ]; then
    echo "Erreur: Le dump n'a pas été créé correctement"
    exit 1
fi

# Redémarrer Neo4j
echo "Redémarrage de Neo4j..."
docker start "${NEO4J_CONTAINER}"

# Attendre que Neo4j soit prêt
echo "Attente de Neo4j..."
sleep 30

# Compresser le backup
echo "Compression du backup..."
if ! tar -czf "${BACKUP_DIR}/${BACKUP_FILE}" -C "${BACKUP_DIR}" neo4j.dump; then
    echo "Erreur: Échec de la compression"
    exit 1
fi

# Vérifier que l'archive a été créée
if [ ! -f "${BACKUP_DIR}/${BACKUP_FILE}" ]; then
    echo "Erreur: L'archive n'a pas été créée"
    exit 1
fi

# Créer le répertoire de sauvegarde sur OwnCloud s'il n'existe pas
echo "Création du répertoire de sauvegarde sur OwnCloud..."
curl -u "${OWNCLOUD_USER}:${OWNCLOUD_PASSWORD}" \
     -X MKCOL \
     "${OWNCLOUD_URL}${OWNCLOUD_BACKUP_DIR}" \
     --fail --silent --show-error || echo "Le répertoire existe déjà ou erreur de création"

# Upload vers OwnCloud via WebDAV
echo "Upload vers OwnCloud..."
if curl -u "${OWNCLOUD_USER}:${OWNCLOUD_PASSWORD}" \
     -T "${BACKUP_DIR}/${BACKUP_FILE}" \
     "${OWNCLOUD_URL}${OWNCLOUD_BACKUP_DIR}/${BACKUP_FILE}"; then
    echo "Upload réussi"
else
    echo "Erreur: Échec de l'upload vers OwnCloud"
    echo "L'archive est conservée localement: ${BACKUP_DIR}/${BACKUP_FILE}"
    exit 1
fi

# Nettoyage local (optionnel - commenter pour garder les backups locaux)
echo "Nettoyage..."
rm -f "${BACKUP_DIR}/neo4j.dump"
# rm "${BACKUP_DIR}/${BACKUP_FILE}"

echo "Sauvegarde terminée: ${BACKUP_FILE}"
