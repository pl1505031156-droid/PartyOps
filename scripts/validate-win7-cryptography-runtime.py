"""验证 Windows 7 安全回移版 cryptography 的真实运行时路径。"""

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def main() -> int:
    """覆盖 OpenSSL 绑定、RSA 私钥恢复、加解密、签名与 Fernet。"""

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    payload = b"PartyOps Windows 7 cryptography runtime gate"
    oaep = padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    )
    ciphertext = public_key.encrypt(payload, oaep)
    if private_key.decrypt(ciphertext, oaep) != payload:
        raise RuntimeError("RSA OAEP 往返校验失败")

    signature = private_key.sign(
        payload,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    public_key.verify(
        signature,
        payload,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )

    numbers = private_key.private_numbers()
    recovered = rsa.rsa_recover_private_exponent(
        numbers.public_numbers.e,
        numbers.p,
        numbers.q,
    )
    if recovered != numbers.d:
        raise RuntimeError("Python 3.8 的 RSA 最小公倍数兼容路径校验失败")

    fernet = Fernet(Fernet.generate_key())
    if fernet.decrypt(fernet.encrypt(payload)) != payload:
        raise RuntimeError("Fernet 往返校验失败")

    print("Windows 7 cryptography 运行时门禁通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
