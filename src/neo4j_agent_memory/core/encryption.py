"""Pluggable at-rest encryption for sensitive memory content.

This module ships only the *protocol* and a no-op default. Production
deployments wanting actual encryption (AES-GCM, KMS-backed, etc.) provide
their own implementation of :class:`MessageContentEncrypter` and pass it
into the relevant memory layer (currently a documentation pattern; v0.5
exposes the protocol so users can write callsites that lift it later).

Why ship just the protocol? Because real encryption pulls in
provider-specific dependencies (AWS KMS, GCP Cloud KMS, HashiCorp Vault,
local libsodium, etc.) and the library should not opinionate. The
protocol is the integration contract.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MessageContentEncrypter(Protocol):
    """Encrypt / decrypt sensitive string content (e.g. Message.content).

    Implementations should be deterministic for a given (key, plaintext)
    pair so that searches by exact content remain possible — but most
    production designs want non-deterministic AEAD encryption, in which
    case content-based search is sacrificed for confidentiality. The
    library doesn't enforce either choice.
    """

    def encrypt(self, plaintext: str) -> str:
        """Return the encrypted ciphertext as a string (typically base64)."""
        ...

    def decrypt(self, ciphertext: str) -> str:
        """Return the decrypted plaintext."""
        ...


class NoOpEncrypter:
    """Default implementation — passes content through unchanged.

    Use this in development or when the database itself is encrypted at
    rest (e.g. Neo4j Aura, AWS RDS) and per-row encryption is unnecessary.
    """

    def encrypt(self, plaintext: str) -> str:
        return plaintext

    def decrypt(self, ciphertext: str) -> str:
        return ciphertext
