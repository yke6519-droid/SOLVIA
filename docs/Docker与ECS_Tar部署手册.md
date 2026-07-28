# SolarAgent Docker 与 ECS Tar 部署手册

本文记录 SolarAgent 从本地模型转换、Docker 构建，到阿里云 ECS 离线部署的完整流程。

本文包含两种部署方式：

1. **源码包部署**：把项目打成 tar 包，上传 ECS，在 ECS 上重新构建镜像。
2. **镜像包部署**：本地构建镜像并导出 tar，上传 ECS 后直接 `docker load`，不依赖 ECS 访问 Docker Hub。

推荐先使用第 2 种方式完成首次部署。因为服务器可能无法访问 Docker Hub，直接导出镜像可以避免 `mysql:8.0` 拉取超时。

---

## 一、部署前的目录约定

项目基线目录：

```text
D:\AAA_myProjects\howso\myAgent\solar_agent-v1.3.0-baseline
```

模型目录：

```text
runtime/models/
├── sunny/
│   ├── lstm_savedmodel/
│   ├── lstnet_savedmodel/
│   ├── xgb_model.model.pkl
│   ├── lgb_model.pkl
│   ├── scaler.pkl
│   ├── feature_cols.txt
│   └── 其他纠偏与融合模型文件
└── cloudy/
    ├── lstm_savedmodel/
    ├── lstnet_savedmodel/
    ├── xgb_model.model.pkl
    ├── lgb_model.pkl
    ├── scaler.pkl
    ├── feature_cols.txt
    └── 其他纠偏与融合模型文件
```

其中：

- `sunny` 存放晴天模型；
- `cloudy` 存放多云、阴天等非晴天模型；
- `lstm_savedmodel` 和 `lstnet_savedmodel` 是 SavedModel 目录；
- `.pkl`、`scaler.pkl`、`feature_cols.txt` 仍然是预测流程所需的辅助文件。

SavedModel 目录不能只复制一个空文件夹，至少要包含：

```text
saved_model.pb
variables/
```

---

## 二、在 Windows 上生成 SavedModel

### 2.1 创建目标目录

```powershell
$baseline = 'D:\AAA_myProjects\howso\myAgent\solar_agent-v1.3.0-baseline'
$sunnyTarget = Join-Path $baseline 'runtime\models\sunny'
$cloudyTarget = Join-Path $baseline 'runtime\models\cloudy'

New-Item -ItemType Directory -Force "$sunnyTarget\lstm_savedmodel" | Out-Null
New-Item -ItemType Directory -Force "$sunnyTarget\lstnet_savedmodel" | Out-Null
New-Item -ItemType Directory -Force "$cloudyTarget\lstm_savedmodel" | Out-Null
New-Item -ItemType Directory -Force "$cloudyTarget\lstnet_savedmodel" | Out-Null
```

如果目标目录中已经存在旧的 SavedModel，不要直接删除。可以先改用新的临时目录验证，确认成功后再替换。

### 2.2 转换晴天 LSTM

下面的源路径是外部训练结果目录，请按实际路径修改：

```powershell
python -c "import tensorflow as tf; src=r'D:\AAA光伏项目\TRAIN\Jiupian\Results\英杰\晴天_5-8\lstm_model.keras'; dst=r'D:\AAA_myProjects\howso\myAgent\solar_agent-v1.3.0-baseline\runtime\models\sunny\lstm_savedmodel'; model=tf.keras.models.load_model(src, compile=False); model.save(dst, save_format='tf'); print('晴天 LSTM SavedModel 生成成功')"
```

### 2.3 转换晴天 LSTNet

```powershell
python -c "import tensorflow as tf; src=r'D:\AAA光伏项目\TRAIN\Jiupian\Results\英杰\晴天_5-8\lstnet_model.keras'; dst=r'D:\AAA_myProjects\howso\myAgent\solar_agent-v1.3.0-baseline\runtime\models\sunny\lstnet_savedmodel'; model=tf.keras.models.load_model(src, compile=False); model.save(dst, save_format='tf'); print('晴天 LSTNet SavedModel 生成成功')"
```

### 2.4 转换多云模型

先把 `$cloudySource` 修改为真实的多云模型训练目录：

```powershell
$cloudySource = 'D:\AAA光伏项目\TRAIN\Jiupian\Results\英杰\多云模型目录'
```

转换多云 LSTM：

```powershell
python -c "import tensorflow as tf; src=r'D:\AAA光伏项目\TRAIN\Jiupian\Results\英杰\多云模型目录\lstm_model.keras'; dst=r'D:\AAA_myProjects\howso\myAgent\solar_agent-v1.3.0-baseline\runtime\models\cloudy\lstm_savedmodel'; model=tf.keras.models.load_model(src, compile=False); model.save(dst, save_format='tf'); print('多云 LSTM SavedModel 生成成功')"
```

转换多云 LSTNet：

```powershell
python -c "import tensorflow as tf; src=r'D:\AAA光伏项目\TRAIN\Jiupian\Results\英杰\多云模型目录\lstnet_model.keras'; dst=r'D:\AAA_myProjects\howso\myAgent\solar_agent-v1.3.0-baseline\runtime\models\cloudy\lstnet_savedmodel'; model=tf.keras.models.load_model(src, compile=False); model.save(dst, save_format='tf'); print('多云 LSTNet SavedModel 生成成功')"
```

### 2.5 检查生成结果

```powershell
Get-ChildItem -Recurse `
  'D:\AAA_myProjects\howso\myAgent\solar_agent-v1.3.0-baseline\runtime\models\sunny'

Get-ChildItem -Recurse `
  'D:\AAA_myProjects\howso\myAgent\solar_agent-v1.3.0-baseline\runtime\models\cloudy'
```

重点确认：

```text
sunny/lstm_savedmodel/saved_model.pb
sunny/lstm_savedmodel/variables/
sunny/lstnet_savedmodel/saved_model.pb
sunny/lstnet_savedmodel/variables/
cloudy/lstm_savedmodel/saved_model.pb
cloudy/lstm_savedmodel/variables/
```

---

## 三、模型辅助文件检查

SavedModel 只替换 LSTM 和 LSTNet，其他预测文件仍然需要保留：

```text
xgb_model.model.pkl
lgb_model.pkl
ridge_xgb_correction.pkl
ridge_lgb_correction.pkl
ridge_lstm_correction.pkl
ridge_lstnet_correction.pkl
meta_stacking_model.pkl
scaler.pkl
feature_cols.txt
```

如果基线目录中没有这些文件，可以从训练目录复制：

```powershell
$source = 'D:\AAA光伏项目\TRAIN\Jiupian\Results\英杰\晴天_5-8'
$target = 'D:\AAA_myProjects\howso\myAgent\solar_agent-v1.3.0-baseline\runtime\models\sunny'

Copy-Item "$source\*.pkl" $target -Force
Copy-Item "$source\scaler.pkl" $target -Force
Copy-Item "$source\feature_cols.txt" $target -Force
```

多云目录同理，但必须使用多云模型对应的辅助文件，不能把晴天和多云的辅助文件混用。

---

## 四、Docker 模型挂载关系

Compose 中的挂载关系是：

```text
Windows 项目目录/runtime/models/sunny
        ↓
容器内 /models/sunny

Windows 项目目录/runtime/models/cloudy
        ↓
容器内 /models/cloudy
```

环境变量应该是：

```env
MODEL_SUNNY=/models/sunny
MODEL_CLOUDY=/models/cloudy
MODEL_FORMAT=auto
```

`MODEL_FORMAT=auto` 的逻辑是：

```text
先找 SavedModel
    ↓ 找到并成功加载
使用 SavedModel

如果不存在或加载失败
    ↓
回退到 .keras
```

如果 ECS 上希望严格只使用 SavedModel，可以设置：

```env
MODEL_FORMAT=savedmodel
```

---

## 五、本地构建 Docker 镜像

进入基线项目目录：

```powershell
cd 'D:\AAA_myProjects\howso\myAgent\solar_agent-v1.3.0-baseline'
```

重新构建后端镜像：

```powershell
docker compose -f deploy\compose.local.yml build backend
```

如果前端也需要重新构建：

```powershell
docker compose -f deploy\compose.local.yml build backend frontend
```

重新创建容器：

```powershell
docker compose -f deploy\compose.local.yml up -d --force-recreate
```

如果代码没有变化，只替换了模型文件，可以不重新构建镜像，直接：

```powershell
docker compose -f deploy\compose.local.yml up -d --force-recreate backend
```

---

## 六、本地 Docker 验证

### 6.1 查看容器状态

```powershell
docker compose -f deploy\compose.local.yml ps
```

### 6.2 查看容器内的模型目录

```powershell
docker compose -f deploy\compose.local.yml exec backend `
  ls -lah /models/sunny
```

```powershell
docker compose -f deploy\compose.local.yml exec backend `
  ls -lah /models/cloudy
```

### 6.3 直接加载 SavedModel

```powershell
docker compose -f deploy\compose.local.yml exec backend `
  python -c "import tensorflow as tf; tf.keras.models.load_model('/models/sunny/lstm_savedmodel', compile=False); print('晴天 LSTM 加载成功')"
```

```powershell
docker compose -f deploy\compose.local.yml exec backend `
  python -c "import tensorflow as tf; tf.keras.models.load_model('/models/sunny/lstnet_savedmodel', compile=False); print('晴天 LSTNet 加载成功')"
```

### 6.4 检查后端健康状态

```powershell
Invoke-RestMethod http://localhost:8001/health
```

返回类似内容即可：

```json
{
  "status": "ok"
}
```

### 6.5 查看日志

```powershell
docker compose -f deploy\compose.local.yml logs --tail=200 backend
```

预测时应该能看到：

```text
加载 SavedModel: /models/sunny/lstm_savedmodel
加载 SavedModel: /models/sunny/lstnet_savedmodel
```

---

## 七、打包整个项目为源码 Tar 包

源码包适合“上传 ECS 后重新构建镜像”的方式。

进入项目的上一级目录：

```powershell
cd 'D:\AAA_myProjects\howso\myAgent'
```

使用 tar 打包基线项目：

```powershell
tar --exclude='solar_agent-v1.3.0-baseline/.git' `
    --exclude='solar_agent-v1.3.0-baseline/.venv' `
    --exclude='solar_agent-v1.3.0-baseline/frontend/node_modules' `
    --exclude='solar_agent-v1.3.0-baseline/frontend/dist' `
    --exclude='solar_agent-v1.3.0-baseline/.pnpm-store' `
    --exclude='solar_agent-v1.3.0-baseline/**/__pycache__' `
    -czf solar-agent-v1.3.0-baseline-project.tar.gz `
    solar_agent-v1.3.0-baseline
```

说明：

- `--exclude` 排除 Git 历史、Python 虚拟环境、Node 依赖和缓存；
- `runtime/models` 会被保留，因此 SavedModel 也会进入源码包；
- 不要把真实 `.env.docker`、API Key、JWT 密钥提交或打包；
- ECS 上应单独创建 `.env.docker`。

检查文件：

```powershell
Get-Item .\solar-agent-v1.3.0-baseline-project.tar.gz
```

---

## 八、导出 Docker 镜像 Tar 包

如果 ECS 无法访问 Docker Hub，推荐使用镜像包部署。

先确认本地镜像存在：

```powershell
docker images
```

导出后端、前端和 MySQL 镜像：

```powershell
docker save -o solar-agent-images-v1.3.0-baseline.tar `
  solar-agent/backend:v1.3.0-baseline `
  solar-agent/frontend:v1.3.0-baseline `
  mysql:8.0
```

检查导出文件：

```powershell
Get-Item .\solar-agent-images-v1.3.0-baseline.tar
```

注意：Docker 镜像 Tar 包只包含镜像，不包含通过 Compose 挂载的模型文件。因此仍然需要上传源码包，或者单独上传 `runtime/models`。

---

## 九、上传 Tar 包到 ECS

在 Windows PowerShell 中执行：

```powershell
scp .\solar-agent-images-v1.3.0-baseline.tar `
  root@你的ECS公网IP:/opt/packages/
```

上传源码包：

```powershell
scp .\solar-agent-v1.3.0-baseline-project.tar.gz `
  root@你的ECS公网IP:/opt/packages/
```

如果两个文件较大，可以分开上传。ECS 上先创建目录：

```bash
mkdir -p /opt/packages
```

---

## 十、ECS 上解压源码包

登录 ECS：

```bash
ssh root@你的ECS公网IP
```

创建项目目录：

```bash
mkdir -p /opt/solar_agent-v1.3.0-baseline
```

解压：

```bash
tar -xzf \
  /opt/packages/solar-agent-v1.3.0-baseline-project.tar.gz \
  -C /opt
```

确认目录：

```bash
cd /opt/solar_agent-v1.3.0-baseline
ls
```

---

## 十一、ECS 替换已有容器并加载新的 Docker 镜像

如果 ECS 上已经运行过旧版本容器，不能只执行 `docker load`。原因是：

```text
docker load
    ↓
只更新本地镜像标签
    ↓
旧容器仍然使用旧的镜像实例
```

正确顺序是：

```text
停止并删除旧容器
    ↓
加载新的镜像 Tar
    ↓
重新创建容器
    ↓
启动新版本
```

### 11.1 先查看当前容器

```bash
cd /opt/solar_agent-v1.3.0-baseline

docker compose -f deploy/compose.local.yml ps -a
```

也可以查看所有 Docker 容器：

```bash
docker ps -a
```

### 11.2 停止并删除旧容器

推荐使用 Compose 删除当前项目的容器和网络：

```bash
docker compose \
  -f deploy/compose.local.yml \
  down --remove-orphans
```

这个命令会：

- 停止 backend、frontend、mysql 等当前 Compose 项目容器；
- 删除这些容器；
- 删除当前 Compose 创建的网络；
- 保留数据库卷和模型目录。

### 11.3 不要随意使用 `down -v`

下面的命令会同时删除 Compose 创建的 Volume：

```bash
docker compose -f deploy/compose.local.yml down -v
```

如果 MySQL 数据库使用了 Docker Volume，执行它可能导致数据库数据被删除。因此生产环境更新版本时不要使用 `-v`。

只有在明确需要清空整个测试数据库时，才使用：

```bash
docker compose -f deploy/compose.local.yml down -v
```

如果旧容器不是由当前 Compose 文件创建的，`docker compose down` 不一定能删除它们。可以先查看：

```bash
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
```

确认名称后，只删除旧容器：

```bash
docker rm -f 旧容器名称
```

这个命令只删除容器，不会自动删除 Docker Volume。不要把容器名替换成数据库 Volume 名称。

### 11.4 加载新的镜像 Tar 包

```bash
docker load -i \
  /opt/packages/solar-agent-images-v1.3.0-baseline.tar
```

确认镜像：

```bash
docker images
```

应看到：

```text
solar-agent/backend   v1.3.0-baseline
solar-agent/frontend  v1.3.0-baseline
mysql                 8.0
```

`docker load` 只是把镜像导入 ECS，不会自动启动容器。

如果新旧镜像使用同一个标签，例如都叫：

```text
solar-agent/backend:v1.3.0-baseline
```

新的镜像会接管这个标签，但旧容器不会自动切换到新镜像，所以仍然必须重新创建容器。

### 11.5 使用新镜像重新创建容器

```bash
docker compose \
  -f deploy/compose.local.yml \
  up -d \
  --no-build \
  --force-recreate \
  --remove-orphans
```

参数含义：

- `-d`：后台启动；
- `--no-build`：不重新构建，直接使用刚刚 `docker load` 的镜像；
- `--force-recreate`：即使配置没有变化，也强制创建新容器；
- `--remove-orphans`：删除不再属于当前 Compose 配置的旧容器。

### 11.6 确认新容器已经运行

```bash
docker compose -f deploy/compose.local.yml ps
```

查看容器实际使用的镜像：

```bash
docker compose -f deploy/compose.local.yml images
```

查看后端日志：

```bash
docker compose \
  -f deploy/compose.local.yml \
  logs --tail=200 backend
```

检查模型挂载：

```bash
docker compose -f deploy/compose.local.yml exec backend \
  ls -lah /models/sunny
```

检查健康接口：

```bash
curl http://127.0.0.1:8001/health
```

### 11.7 数据安全检查

更新容器前，可以先确认数据库卷名称：

```bash
docker volume ls
```

如果 Compose 使用了 MySQL Volume，执行普通的：

```bash
docker compose -f deploy/compose.local.yml down
```

不会删除该 Volume。重新执行 `up` 后，MySQL 会继续使用原来的数据库数据。

完整的安全更新命令如下：

```bash
cd /opt/solar_agent-v1.3.0-baseline

docker compose -f deploy/compose.local.yml down --remove-orphans

docker load -i \
  /opt/packages/solar-agent-images-v1.3.0-baseline.tar

docker compose \
  -f deploy/compose.local.yml \
  up -d \
  --no-build \
  --force-recreate \
  --remove-orphans

docker compose -f deploy/compose.local.yml ps
```

---

## 十二、ECS 创建生产环境配置

进入项目目录：

```bash
cd /opt/solar_agent-v1.3.0-baseline
```

复制环境变量模板：

```bash
cp .env.docker.example .env.docker
```

编辑配置：

```bash
nano .env.docker
```

生产环境至少检查：

```env
MYSQL_URL=mysql+pymysql://solar_agent:数据库密码@mysql:3306/solar_agent?charset=utf8mb4
MODEL_SUNNY=/models/sunny
MODEL_CLOUDY=/models/cloudy
MODEL_FORMAT=auto
JWT_SECRET_KEY=真实的32位以上随机密钥
```

这里数据库地址必须使用：

```text
mysql:3306
```

不能写：

```text
localhost:3307
```

因为 backend 和 mysql 都在 Docker Compose 网络中，`mysql` 是 MySQL 容器的服务名，`3306` 是容器内部端口。

真实 `.env.docker` 只保存在 ECS，不要提交到 Git。

---

## 十三、ECS 启动系统

如果镜像已经通过 `docker load` 导入，不需要再次构建：

```bash
docker compose -f deploy/compose.local.yml up -d --no-build
```

如果你希望允许 Compose 在本地文件缺少镜像时构建，可以使用：

```bash
docker compose -f deploy/compose.local.yml up -d
```

但离线部署更推荐 `--no-build`，避免 Compose 尝试从 Docker Hub 拉取镜像。

查看状态：

```bash
docker compose -f deploy/compose.local.yml ps
```

查看后端日志：

```bash
docker compose -f deploy/compose.local.yml logs --tail=200 backend
```

检查容器内模型：

```bash
docker compose -f deploy/compose.local.yml exec backend \
  ls -lah /models/sunny
```

检查 SavedModel：

```bash
docker compose -f deploy/compose.local.yml exec backend \
  python -c "import tensorflow as tf; tf.keras.models.load_model('/models/sunny/lstm_savedmodel', compile=False); print('ECS 晴天 LSTM 加载成功')"
```

检查健康接口：

```bash
curl http://127.0.0.1:8001/health
```

---

## 十四、源码包部署和镜像包部署的区别

### 源码包部署

```text
本地项目
    ↓ tar.gz
上传 ECS
    ↓ 解压
ECS docker compose build
    ↓
ECS 本地构建镜像
    ↓
docker compose up
```

优点：

- 适合代码经常变化的阶段；
- ECS 上的代码和镜像来源一致；
- 不需要本地打包镜像。

缺点：

- ECS 需要下载 Python、Node、MySQL 等基础镜像；
- ECS 需要能访问镜像仓库；
- 构建时间较长。

### 镜像包部署

```text
本地构建镜像
    ↓ docker save
镜像 Tar 包
    ↓ 上传 ECS
docker load
    ↓
docker compose up --no-build
```

优点：

- ECS 不需要重新构建；
- 不依赖 Docker Hub；
- 适合网络受限的服务器。

缺点：

- 镜像 Tar 包体积较大；
- 每次代码变更都要重新构建、导出和上传；
- 模型挂载目录仍然需要单独上传。

---

## 十五、常见问题

### 1. 找不到 compose 文件

```text
open C:\Users\chen\deploy\compose.local.yml
```

说明当前目录不对。先执行：

```powershell
cd 'D:\AAA_myProjects\howso\myAgent\solar_agent-v1.3.0-baseline'
```

### 2. Docker 拉取 mysql:8.0 超时

说明 ECS 无法访问 Docker Hub。使用本地镜像包：

```bash
docker load -i /opt/packages/solar-agent-images-v1.3.0-baseline.tar
docker compose -f deploy/compose.local.yml up -d --no-build
```

### 3. Docker 能加载 SavedModel，但业务仍读取 .keras

确认容器使用的是重新构建后的 backend 镜像：

```bash
docker compose -f deploy/compose.local.yml up -d --build --force-recreate backend
```

然后检查日志是否出现：

```text
加载 SavedModel: /models/sunny/lstm_savedmodel
```

### 4. 只挂载 SavedModel 后预测缺少 pkl 文件

说明挂载目录中只有神经网络模型。预测还需要：

```text
scaler.pkl
feature_cols.txt
xgb_model.model.pkl
lgb_model.pkl
各种 ridge 和 meta 模型
```

应挂载完整的 `runtime/models/sunny` 和 `runtime/models/cloudy` 目录。

### 5. 修改模型后预测仍使用旧模型

模型通常在进程启动时加载并缓存在 `ModelManager` 中。替换模型文件后要重启 backend：

```bash
docker compose -f deploy/compose.local.yml up -d --force-recreate backend
```

---

## 十六、推荐的首次 ECS 部署顺序

```text
1. Windows 转换 sunny/cloudy SavedModel
2. 将 SavedModel 放入 runtime/models/sunny 和 cloudy
3. 本地构建 backend、frontend、mysql 镜像
4. 本地执行 Docker 模型加载测试
5. 本地执行 docker save 导出镜像 Tar
6. 项目源码和镜像 Tar 上传 ECS
7. ECS 执行 docker load
8. ECS 创建 .env.docker
9. ECS 执行 docker compose up -d --no-build
10. 检查容器状态、模型挂载、health 接口和后端日志
11. 从浏览器发起一次真实预测
```

这套方式的核心是：

```text
代码通过镜像交付
模型通过宿主机目录挂载
配置通过 .env.docker 注入
数据库通过 MySQL 容器持久化
```
