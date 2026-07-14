# 阶段一验收清单

## 静态检查

- [ ] Python compileall 通过
- [ ] requirements.txt 包含 PyJWT 和 argon2-cffi
- [ ] `.env.example` 包含 JWT 配置
- [ ] 源码不再从请求体或 query 参数读取 user_id
- [ ] memory_schema.sql 不创建测试清理 Event

## 认证

- [ ] 正确登录返回 access_token
- [ ] 缺少 Token 返回 401
- [ ] 伪造或过期 Token 返回 401
- [ ] 创建会话自动绑定当前用户
- [ ] 用户 A 无法读取或删除用户 B 的会话

## 密码

- [ ] 新用户使用 Argon2id
- [ ] 旧 SHA256 用户不能登录
- [ ] 新注册用户只能写入 Argon2id 哈希
- [ ] 错误账号和错误密码不暴露具体原因

## Agent 交互

- [ ] ask_user 显示真实问题
- [ ] 回复后 Agent 继续执行
- [ ] 超时后返回明确结果
- [ ] 同一会话并发请求返回 409
- [ ] 多会话不会串回复
- [ ] SSE 断开后 Bridge 被清理

## 数据库

- [ ] 新环境建表成功
- [ ] 旧环境迁移成功
- [ ] 测试清理 Event 已删除
- [ ] 消息和摘要写入 user_id
- [ ] 服务重启后可以恢复会话

## 运行验证

由于当前 `.venv` 的 Python 路径已失效，完整运行验证需要先重建 `.venv`，再执行 FastAPI TestClient 和 MySQL 集成测试。