# Tests README

## 1. 개요
본 디렉토리는 **스크립트 기반으로 AWS 인프라를 검증**하고, 동작을 **관찰/분석**하기 위한 테스트 코드 모음입니다.

- **Pytest + Boto3**: 인프라 리소스 상태/동작 자동 검증
- **Locust**: 정밀한 성능·부하 측정, SLA 기반 한계치 탐색 및 서비스 가용성 검증

### 1.1 목적 및 기대효과
- 인프라가 의도대로 구성되고 동작하는지 스크립트를 통한 자동 검증
- 인프라 구조와 동작을 정량적으로 측정하고, 적용된 정책의 적절성 분석
- 측정 데이터를 기반으로 인프라 안정성·성능·비용 최적화 방향 도출

---

## 2. AWS 인프라 테스트하기
본 테스트 코드는 다양한 AWS 인프라 환경에서 동작하도록 설계되었습니다. 테스트하는 인프라는 다음 조건을 만족해야 합니다.


### 2.1 요구 조건
인프라에 로드밸런싱, 오토스케일링 그룹이 구축되어있고, 웹서비스가 아래 조건을 만족해야 합니다.

- 필수 엔드포인트 구현: `/`, `/health`, `/kill`, `/work`
- 테스트 수행 환경에서 대상 ALB로의 트래픽이 보안그룹이나 방화벽 등으로 차단되지 않아야 함.

### 2.2 필요한 설정값
테스트는 아래 3개 값을 알면 실행 가능합니다.

- `ALB_URL` : ALB 접속 URL (예: `http://xxxx.ap-northeast-2.elb.amazonaws.com`)
- `ASG_NAME` : Auto Scaling Group 이름
- `TG_ARN` : Target Group ARN
> Terraform을 쓰지 않는 인프라라면,
> 콘솔에서 위 3개 값을 확인해 `tests/.env`에 저장합니다.

### 2.3 설정 방법 (둘 중 한가지)

#### 방법 A) Terraform output 연동
Terraform으로 구축된 인프라의 경우, output값을 JSON 파일로 내보내서 연동할 수 있습니다.

단, Terraform 코드의 `output`에 다음 값들이 포함되어야 합니다:
* `alb_dns_name`
* `target_group_arn`
* `asg_name`

메인 인프라폴더인 `env/dev/`에서 `terraform apply`를 하셔서 인프라를 구축하신 경우, `env/dev/`폴더에서
```bash
terraform output -json > ../../tests/infra_config.json
```
을 통해 output 값을 연동할 수 있습니다.


일반적으로는 Terraform이 실행된 위치에서 다음 명령어를 통해 설정 파일을 생성하세요.

```bash
# <path-to-repo> 부분을 이 리포지토리가 위치한 실제 경로로 변경하세요.
terraform output -json > <path-to-repo>/tests/infra_config.json
```

#### 방법 B) `.env`로 직접 지정
`tests/.env` 파일을 생성하고 값을 직접 입력합니다. (Terraform 사용하지 않는 경우 권장)

```ini
ALB_URL=http://...
ASG_NAME=my-asg-name
TG_ARN=arn:aws:elasticloadbalancing:...
```



---

## 3. 기술 스택
- **Language**: Python 3.10+ (3.13 권장)
- **Test Framework**: Pytest
- **AWS SDK**: Boto3
- **Load Test**: Locust

---

## 4. 디렉토리 구조
```plaintext
.
├── tests/
│   ├── functional/                 # 기능/시나리오 기반 테스트
│   │   ├── test_connectivity.py    # [기본] 연결/등록/분산 확인
│   │   ├── test_recovery.py        # [분석] 장애 후 복구 측정
│   │   └── test_scaling.py         # [분석] 스케일링 동작 관찰/측정
│   ├── performance/                # 성능 측정 및 부하 테스트
│   │   ├── locustfile.py           # Locust 부하 테스트 시나리오
│   │   └── locust_metrics.py       # 성능 metric 측정 및 SLA 검증
│   ├── conftest.py                 # AWS 연결/terraform output 연동 등
│   ├── __init__.py
│   ├── utils.py
│   ├── .env                        # (선택) 인프라 값 직접 지정
│   └── infra_config.json           # (선택) terraform output 값
├── pytest.ini                   # pytest 설정
├── requirements.txt             # 의존성
└── README.md                    # 본 문서
```

---

## 5. 시작하기 (Getting Started)

### 5.1 사전 준비
- Python 3.10+ (권장: 3.13)
- AWS CLI 인증 설정 (`aws configure`)
  - 본 테스트는 `boto3`로 AWS API를 호출하므로, **테스트할 인프라에 접근 가능한 자격증명**이 필요합니다.

### 5.2 의존성 설치
프로젝트 루트(※ `tests/` 폴더가 아니라 프로젝트 최상단)에서 실행:

```bash
pip install -r requirements.txt
```

---

## 6. 기초 동작 테스트 실행 (Pytest)
`testpaths=tests/functional` 로 설정되어 있어, `tests/` 디렉토리 내부가 아닌 **프로젝트 루트에서 실행**합니다.

### 6.1 Connectivity 테스트
```bash
pytest -m connectivity
```

검증 항목
- ALB URL 접근 가능(200 응답)
- Target Group에 Healthy 인스턴스가 최소 2대 이상 존재
- 트래픽이 단일 인스턴스에 편향되지 않고 분산되는지
- Healthy 인스턴스가 2개 이상의 AZ에 분산되는지

---

### 6.2 장애(Recovery) 테스트
```bash
pytest -m fault
```

검증 항목
- 시나리오 A: 애플리케이션 장애(`/kill`)
  - ALB가 Unhealthy 감지하는지
  - ASG가 자동 복구 수행하는지(교체 인스턴스 Healthy 도달)
  - 장애~복구 과정에서 요청 성공률(가용성) 측정
- 시나리오 B: 인프라 장애(인스턴스 강제 종료)
  - ASG 자동 복구 수행하는지 (교체 인스턴스 Healthy 도달)
  - 복구 과정에서 요청 성공률(가용성) 측정

---

### 6.3 스케일링(Scaling) 테스트
```bash
pytest -m scaling
```

검증 항목
- `/work` 호출로 부하 생성 → Scale-out 발생(Desired Capacity 증가) 확인
- Healthy Target 수 증가 확인
- 부하 중단 후 Scale-in 발생(Desired Capacity 감소) 확인
- Healthy Target 수 감소 확인

> **참고**
> 스케일링 결과는 인스턴스 타입/웹서버 구현/네트워크 상태에 따라 달라질 수 있습니다.
> 
> 특히 Scale-in은 시간이 오래 걸릴 수 있습니다(15분 이상).  

---
## 7. 정밀 성능 및 가용성 테스트 실행 (Locust)
> **Pytest(6번)와의 차이점:**
> Pytest가 인프라 리소스의 상태를 API로 조회하여 의도대로 인프라가 동작하는지를 검증한다면, Locust는 실제 사용자 트래픽을 발생시켜 부하 분산, 장애 발생 시 클라이언트가 체감하는 가용성 저하 및 복구 과정, 시스템 한계치를 정밀하게 관찰합니다.

본 테스트도 6번처럼 프로젝트 루트에서 실행함을 기준으로 작성되었습니다.

---
### 7.1 Locust 테스트 동작 원리
본 테스트 스크립트는 환경 변수(`.env`) 설정에 따라 **역할이 다른 3종류의 가상 유저**를 혼합하여 실제 운영 환경과 유사한 복합적인 상황을 시뮬레이션합니다.

* **ObserveUser (관측 유저 - 트래픽의 80% 차지):** 끊임없이 기본 페이지(`/`)에 접속하여 서버의 응답 시간, 성공/실패 여부, 그리고 어느 인스턴스가 요청을 처리했는지(로드밸런싱)를 기록하고 감시합니다. 
  * (※ 스케일링 테스트가 꺼져있을 경우 트래픽의 100%를 차지합니다.)
* **ScalingUser (부하 유저 - 트래픽의 20% 차지):** `ENABLE_SCALING=1` 설정 시 투입됩니다. 무거운 작업(`/work`)을 호출하여 인스턴스의 CPU 사용률을 고의로 높여 Auto Scaling(Scale-out)을 유도합니다.
* **FaultUser (장애 유저 - 단 1명만 투입):** `ENABLE_FAULT=1` 설정 시 전체 유저 중 딱 1명만 생성됩니다. `/kill` 엔드포인트를 호출해 애플리케이션 장애 상황을 시뮬레이션 합니다. 이후 `ObserveUser`들이 시스템이 어떻게 복구되는지를 관측합니다.

---

### 7.2 테스트 환경 변수 설정 (`tests/.env`)
테스트 시나리오는 `tests/.env` 파일의 변수값을 통해 제어합니다. 활성화하려는 기능에 따라 아래 표를 참고하여 설정하세요.

#### ① 공통 및 관측(Observe) 기본 설정
항상 동작하는 `ObserveUser`의 행동 양식과 타겟을 결정합니다.
| 환경 변수 | 기본값 | 설명 |
| :--- | :--- | :--- |
| `ALB_URL` | (없음) | 타겟 ALB 주소 (※ `tests/infra_config.json` 존재 시 자동 로드되므로 생략 가능) |
| `OBS_WAIT_MIN` | `0.5` | 관측 유저가 다음 요청을 보내기 전 대기하는 최소 시간(초) |
| `OBS_WAIT_MAX` | `1.5` | 관측 유저가 다음 요청을 보내기 전 대기하는 최대 시간(초) |
| `SLA_P95_MS` | `1000` | 시스템 목표 응답 속도(P95, ms). 계단식 부하 테스트 진행 시 테스트 자동 중단(Break Point)의 기준이 되기도 합니다. |

#### ② 장애 주입 (Fault) 설정 
장애 복구력 및 가용성 테스트를 진행할 때 사용합니다. (`ENABLE_FAULT=1` 일 때 유효)
| 환경 변수 | 기본값 | 설명 |
| :--- | :--- | :--- |
| `ENABLE_FAULT` | `0` | `1`로 설정 시 장애 유저(`FaultUser`) 1명을 시나리오에 투입합니다. |
| `FAULT_MODE` | `single` | `single` (단일 서버 장애) 또는 `all` (모든 서버 장애) |

#### ③ 부하 및 스케일링 (Scaling) 설정
Auto Scaling 동작 확인 및 한계치(Break Point) 탐색 시 사용합니다. (`ENABLE_SCALING=1` 일 때 유효)
| 환경 변수 | 기본값 | 설명 |
| :--- | :--- | :--- |
| `ENABLE_SCALING` | `0` | `1`로 설정 시 CPU 부하를 유도하는 유저(`ScalingUser`)를 투입합니다.|
|`SCALING_WEIGHT` | `20` | 전체 유저 중 부하 유저(`ScalingUser`)의 비율(%)을 설정합니다.|
| `WORK_SEC` | `5.0` | 부하 유저가 `/work` 호출 시 서버에 요구할 연산 시간(초). 값이 클수록 부하가 강해집니다. |
|`SCALING_DURATION_SEC` | `0` | 부하 유지 시간(초). 0보다 큰 값으로 설정 시, 해당 시간이 지나면 부하 유저(`ScalingUser`)는 동작을 멈추고 대기 상태로 전환됩니다. 0으로 설정시, 계속 부하가 유지됩니다.|
| `USE_STEP_SHAPE` | `0` | `1`로 설정 시 계단식으로 유저 수를 점진적으로 늘려가는 부하 테스트를 진행합니다. |
| `STEP_USERS` | `50` | (계단식 부하) 한 단계(Step)가 넘어갈 때마다 추가로 투입할 유저 수 |
| `STEP_TIME` | `180` | (계단식 부하) 한 단계를 유지할 시간(초) |
| `ENABLE_STEP_SLA_STOP` | `0` | `1`로 설정 시 응답 속도가 목표치(SLA)를 위반하면 인프라가 한계에 달한 것으로 보고 테스트를 자동 중단합니다. |


---

### 7.3 Locust 명령어 주요 옵션 안내
- `-f tests/performance/locustfile.py` : 실행할 Locust 스크립트 파일 경로
- `-u <숫자>` : 총 생성할 가상 사용자 수 (Users)
- `-r <숫자>` : 1초당 생성할 사용자 수 (Spawn Rate)
- `-t <시간>` : 테스트 실행 시간 (예: `2m`=2분, `15m`=15분)
- `--headless` : (선택) 웹 UI 없이 터미널에서 백그라운드로 바로 실행할 때 추가

---

### 7.4 시나리오별 실행 가이드

#### 1) 로드밸런싱 검증 테스트
트래픽이 존재하는 상황에서 다수의 요청이 여러 인스턴스로 균등하게 분산되는지 확인합니다.

**`tests/.env` 설정:**
```ini
ENABLE_SCALING=0
ENABLE_FAULT=0
OBS_WAIT_MIN=0.25
OBS_WAIT_MAX=0.75
```
**실행 명령어:**
```bash
locust -f tests/performance/locustfile.py -u 100 -r 10 -t 2m
```

#### 2) 단일 장애 자동 복구 테스트
운영 중인 서버 1대에 장애(`/kill`)를 주입합니다. 사용자가 체감하는 가용성(성공률) 저하, 비정상 인스턴스의 트래픽 배제, 그리고 신규 인스턴스 투입을 통한 복구 과정을 관찰합니다.

**`tests/.env` 설정:**
```ini
ENABLE_SCALING=0
ENABLE_FAULT=1
FAULT_MODE=single
OBS_WAIT_MIN=0.25
OBS_WAIT_MAX=0.75
```
**실행 명령어 (장애 주입 유저 1명을 포함해 101명으로 설정):**
```bash
locust -f tests/performance/locustfile.py -u 101 -r 10 -t 10m
```

#### 3) 전면 장애 테스트
Human Error 등으로 인해 모든 인스턴스가 동시에 다운되는 극단적인 상황을 시뮬레이션합니다. 시스템이 다시 정상 응답을 제공하기까지의 총 소요 시간과 그동안 발생하는 에러 수치를 확인합니다.

**`tests/.env` 설정:**
```ini
ENABLE_SCALING=0
ENABLE_FAULT=1
FAULT_MODE=all
OBS_WAIT_MIN=0.25
OBS_WAIT_MAX=0.75
```
**실행 명령어:**
```bash
locust -f tests/performance/locustfile.py -u 101 -r 10 -t 12m
```

#### 4) 스케일링 테스트 (Constant Load)
무거운 작업(`/work`)을 호출하여 CPU 부하를 발생시킵니다. 설정된 조건에 따라 Scale-out이 트리거되는 소요 시간과, 스케일링이 진행되는 동안 클라이언트 관점에서 시스템이 안정적으로 응답하는지 관찰합니다.

(※ `WORK_SEC`과 `-u` 값을 변경하여 부하 정도를 조절할 수 있습니다.)

또한, 부하 유지 시간을 설정하여 Scale-out 이후, Scale-in 과정까지 한 번에 관측할 수 있습니다.


**`tests/.env` 설정:**
```ini
ENABLE_SCALING=1
ENABLE_FAULT=0
USE_STEP_SHAPE=0
OBS_WAIT_MIN=0.1
OBS_WAIT_MAX=0.4
WORK_SEC=3.5
SCALING_DURATION_SEC=600
```
**실행 명령어:**
```bash
locust -f tests/performance/locustfile.py -u 25 -r 5 -t 15m
```

#### 5) 인프라 한계치 탐색 테스트 (Step-up / Break Point)
계단식으로 트래픽과 부하를 점진적으로 증가시키며, 시스템이 설정된 목표 성능(SLA)을 위반하는 지점을 자동으로 탐색합니다. (해당 모드는 스크립트 내부에서 유저 수를 제어하므로 명령어에 `-u`, `-r`, `-t` 옵션을 주지 않습니다.)

**`tests/.env` 설정:**
```ini
ENABLE_SCALING=1
ENABLE_FAULT=0
USE_STEP_SHAPE=1
ENABLE_STEP_SLA_STOP=1
SLA_P95_MS=1000
STEP_TIME=300
TIME_LIMIT=1800
STEP_USERS=50
WORK_SEC=2
OBS_WAIT_MIN=0.25
OBS_WAIT_MAX=0.75
```
**실행 명령어:**
```bash
locust -f tests/performance/locustfile.py
```

#### 6) 지속 가용성 및 기본 응답 속도 측정 테스트
일정 시간 동안 시스템에 무리를 주지 않는 가벼운 트래픽을 지속적으로 발생시켜, 서비스 중단이나 장애가 발생하는지 점검하고 평균 응답 속도를 측정합니다.
평상시 인프라의 기준 성능(Baseline)을 확인하거나, 서버 배포가 진행되는 동안 서비스가 끊기지 않는지 검증하는 목적으로 활용할 수 있습니다.

**`tests/.env` 설정:** (스케일링/장애 주입을 끄고 대기 시간을 늘려 부하를 낮춤)
```ini
ENABLE_SCALING=0
ENABLE_FAULT=0
OBS_WAIT_MIN=0.5
OBS_WAIT_MAX=1.5
```
**실행 명령어:** (측정하고자 하는 시간에 맞춰 30분 등 넉넉하게 설정하거나, 필요시 `-t` 옵션을 빼고 수동 중단)
```bash
locust -f tests/performance/locustfile.py -u 50 -r 5 -t 5m #BASE LINE 측정시
locust -f tests/performance/locustfile.py -u 50 -r 5 -t 30m #지속적인 가용성 측정시
```
---
## 8. 테스트 결과 저장 (선택)
테스트 실행결과를 HTML 리포트 파일로 저장할 수 있습니다.

### 8.1 기초 동작 테스트 (Pytest)

```bash
pytest -m connectivity --html=reports/connectivity.html --self-contained-html
pytest -m fault --html=reports/fault.html --self-contained-html
pytest -m scaling --html=reports/scaling.html --self-contained-html
```

### 8.2 정밀 부하/장애 테스트 (Locust)
Locust 테스트 실행시 `--headless` 모드와 `--html` 옵션을 추가하면, 지정된 시나리오가 백그라운드에서 실행된 후 그래프가 포함된 HTML 리포트가 생성됩니다.
불필요하게 많은 터미널 출력을 막기 위해 `--only-summary` 옵션도 추가합니다.

```bash
# 예시: 로드밸런싱 테스트 (2분) 결과를 HTML로 저장
locust -f tests/performance/locustfile.py -u 100 -r 10 -t 2m --headless --html=reports/locust_load_tests.html --only-summary
```

---

> **※Note on cleanup**: 기존의 `tests/fixtures/` 디렉토리는 불필요한 혼란 방지 및 최종 코드 정돈을 위해 프로젝트에서 제외하였습니다.
## 9. TODO & Future Work
- 메인 인프라에서 테스트 스크립트가 잘 구동될 수 있도록 세부 설정값 등 정밀 조정
- 메인 인프라에서 정상적으로 시나리오 수행 최종 확인
- 테스트 결과를 통해 메인 인프라의 실제 동작과 안정성 최종 측정 및 분석

---
