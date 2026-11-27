# KW45 签名与加密密钥目录 (`keys/`)

本目录存放从 NXP 官方参考仓库提取的 KW45 原厂测试密钥，供 `make-ram-boot.py --sb3` 模式默认调用（同时签出 `*_signed.bin` 与 `*.sb3`）。

## 文件清单

| 文件名 | 用途说明 |
| :--- | :--- |
| `ROT1_p384.pem` | ECDSA P-384 私钥（用于签署 MBI 镜像与 SB3.1 容器） |
| `ROT1_p384.pub` | 对应的 ECDSA P-384 公钥 |
| `ROT1_cert_block.bin` | Root of Trust 证书块（含 ROTKTH 校验信息） |
| `sb3kdk.txt` | SB3.1 容器 AES 加密所需的 Key Derivation Key (SB3KDK) |

> [!WARNING]
> 以上密钥仅适用于 **NXP KW45 EVK 出厂测试或未锁死（`OEM Open`）的开发板**。

---

## 客户自定义密钥生成与替换说明

如果目标板已开启 **Secure Boot（`OEM Closed` 生命周期）**，芯片 OTP/Fuse 中烧录的是客户自己的 ROTKTH 与 CUST_MK_SK / SB3KDK，必须将本目录的测试密钥替换为客户自己的密钥文件（保持相同文件名，或通过 `-k <自定义密钥目录>` 指定）。

客户可参考 NXP 官方开源仓库中的 `1.generate_keys.cmd` 与相关配置自行生成全套密钥与证书块：
- **GitHub 官方仓库**：[https://github.com/nxp-appcodehub/dm-kw45-secure-boot-scripts](https://github.com/nxp-appcodehub/dm-kw45-secure-boot-scripts)

生成后，将产出的 `ROT1_p384.pem`、`ROT1_p384.pub`、`ROT1_cert_block.bin` 与 `sb3kdk.txt` 覆盖替换到本目录即可。
