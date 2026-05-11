# Ombre-Brain VPS 部署备忘录

## 1. 当前部署结构

当前 Ombre-Brain 运行在 VPS 上，采用 Docker 部署，通过 Cloudflare Tunnel 对外暴露服务。

- 部署节点：VPS（Ubuntu）
- 容器运行：Docker + docker-compose
- 外网入口：Cloudflare Tunnel（非 nginx / caddy）
- MCP transport：streamable-http
- 外网 MCP 地址：https://ob.lumileoforever.win/mcp
- 健康检查地址：https://ob.lumileoforever.win/health

## 2. 当前目录结构

项目主目录：/home/ubuntu/ombre-brain

核心结构如下：

- source：Ombre-Brain 源码目录（GitHub fork 克隆内容）
- uckets：记忆数据目录（宿主机持久化）
- docker-compose.yml：当前部署编排文件
- .env：运行环境变量文件（包含 API 与运行参数）

## 3. buckets 挂载方式

当前数据目录挂载关系如下：

- 宿主机目录：/home/ubuntu/ombre-brain/buckets
- 容器内目录：/data
- 环境变量：OMBRE_BUCKETS_DIR=/data

说明：容器内 Ombre-Brain 只应访问 /data，并通过 volume 与宿主机目录保持一致，确保数据持久化。

## 4. Railway 迁移历史

迁移历史关键点：

- Railway 早期阶段，数据曾错误写入临时目录（非持久化 volume）。
- 后续已迁移到 Railway 的真实持久化 volume。
- 迁移过程中已制作本地 	ar.gz 备份。
- 在 VPS 恢复时，曾出现 uckets/buckets 双层目录问题。
- 双层目录问题已修复，现为单层正确结构。

## 5. Dockerfile 修复

在当前 Docker 环境中，Dockerfile 原写法：

`dockerfile
COPY *.py .
`

会触发构建失败（多源 COPY 目标需为目录且以 / 结尾）。

已修复为：

`dockerfile
COPY *.py ./
`

该修复已验证可通过 docker-compose build。

## 6. Cloudflare Tunnel

当前 tunnel 结论：

- 使用 Cloudflare Dashboard 管理的 tunnel。
- 反代体系不是 nginx / caddy。
- ob.lumileoforever.win 指向 localhost:8000。

## 7. 当前运行方式

### 启动

`ash
sudo docker-compose up -d
`

### 停止

`ash
sudo docker-compose down
`

### 日志

`ash
sudo docker logs -f ombre-brain
`

### 重启

`ash
sudo docker-compose restart
`

## 8. 备份方式

### 备份

在 /home/ubuntu/ombre-brain 下执行：

`ash
tar czf ombre-backup-日期.tar.gz buckets
`

### 恢复

按实际路径恢复，例如：

`ash
tar xzf ombre-backup-日期.tar.gz
`

或按目标目录恢复：

`ash
tar xzf ombre-backup-日期.tar.gz -C /home/ubuntu/ombre-brain
`

### 恢复注意事项（重要）

- 不要覆盖一个已确认为空但正在被容器初始化写入的 uckets。
- 不要先启动空容器再恢复数据，否则可能产生空结构或覆盖风险。
- 建议流程：先恢复数据目录结构与内容，再启动容器。

## 9. Git 安全

以下内容必须保持不提交：

- .env
- uckets
- *.db
- *.tar.gz

说明：这些内容包含敏感配置、运行态数据或备份文件，不应进入版本库。

## 10. 当前状态

截至当前记录：

- 184 个 buckets 可正常读取。
- Kelivo 已成功连接 MCP。
- Railway 仍保留，作为过渡期备份。

---

安全提醒：本备忘录不记录真实 API Key；生产密钥仅保留在受控环境变量或密钥管理系统中。
