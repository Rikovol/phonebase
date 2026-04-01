# PhoneBase — Руководство по развёртыванию на сервере (152-ФЗ)

## 1. Выбор и настройка сервера

### Рекомендуемые хостинги (ЦОД в РФ)
- **Selectel** — https://selectel.ru (рекомендуется, есть аттестованные ЦОДы)
- **Timeweb Cloud** — https://timeweb.cloud
- **Рег.ру** — https://www.reg.ru

### Минимальные требования
- CPU: 2 vCPU
- RAM: 4 GB (8 GB рекомендуется)
- Диск: 50 GB SSD
- ОС: Ubuntu 24.04 LTS

---

## 2. Первичная настройка сервера

```bash
# Обновление системы
apt update && apt upgrade -y

# Установка Docker
curl -fsSL https://get.docker.com | sh
systemctl enable docker

# Создание пользователя приложения (не root!)
useradd -m -s /bin/bash phonebase
usermod -aG docker phonebase

# Базовый файрвол (UFW)
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp       # SSH
ufw allow 80/tcp       # HTTP (редирект на HTTPS)
ufw allow 443/tcp      # HTTPS
ufw enable
```

---

## 3. Шифрование диска для персональных данных (LUKS)

```bash
# Создаём отдельный раздел для ПД (если есть второй диск /dev/sdb)
cryptsetup luksFormat /dev/sdb
cryptsetup luksOpen /dev/sdb pd_secure
mkfs.ext4 /dev/mapper/pd_secure

# Монтируем
mkdir -p /mnt/pd_secure
mount /dev/mapper/pd_secure /mnt/pd_secure
chmod 700 /mnt/pd_secure

# В docker-compose.yml монтируем этот путь как том pd_documents:
# volumes:
#   pd_documents:
#     driver: local
#     driver_opts:
#       type: none
#       o: bind
#       device: /mnt/pd_secure
```

---

## 4. Развёртывание приложения

```bash
# Клонируем проект
su - phonebase
git clone <your-repo> /home/phonebase/phonebase
cd /home/phonebase/phonebase

# Создаём .env из шаблона и заполняем
cp .env.example .env
nano .env

# Генерация ключей (выполнить и вставить в .env)
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))"
python3 -c "from cryptography.fernet import Fernet; print('PD_ENCRYPTION_KEY=' + Fernet.generate_key().decode())"

# Запуск
docker compose up -d

# Инициализация БД и создание первого admin-пользователя
docker compose exec backend python -m app.cli create_admin
```

---

## 5. Получение TLS-сертификата

```bash
# Let's Encrypt (бесплатно)
apt install certbot
certbot certonly --standalone -d yourdomain.ru -d www.yourdomain.ru

# Копируем в том SSL
cp /etc/letsencrypt/live/yourdomain.ru/fullchain.pem /path/to/ssl_certs/
cp /etc/letsencrypt/live/yourdomain.ru/privkey.pem /path/to/ssl_certs/

# Автообновление сертификата
echo "0 3 * * * root certbot renew --quiet && docker compose restart nginx" >> /etc/crontab
```

---

## 6. Резервное копирование (обязательно по 152-ФЗ)

```bash
# Создаём скрипт бэкапа /home/phonebase/backup.sh
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M)
BACKUP_DIR=/home/phonebase/backups

# Дамп PostgreSQL
docker compose exec -T postgres pg_dump -U phonebase phonebase | \
  gpg --symmetric --cipher-algo AES256 -o "$BACKUP_DIR/db_$DATE.sql.gpg"

# Синхронизируем на второй сервер или резервный диск
# rsync -avz --delete $BACKUP_DIR/ backup-server:/backups/phonebase/

# Удаляем бэкапы старше 30 дней
find $BACKUP_DIR -name "*.gpg" -mtime +30 -delete

# Запускаем ежедневно в 2:00
echo "0 2 * * * phonebase /home/phonebase/backup.sh" >> /etc/crontab
```

---

## 7. Обязательные юридические шаги (152-ФЗ)

### До запуска системы с паспортными данными:

- [ ] **Уведомление Роскомнадзора** (операторы ПД)
      https://pd.rkn.gov.ru/operators-registry/operators-list/
      Срок: до начала обработки ПД

- [ ] **Политика обработки персональных данных**
      Документ должен быть на сайте в открытом доступе
      Шаблон: https://rkn.gov.ru/personal-data/p868/

- [ ] **Приказ о назначении ответственного за ПД**
      Внутренний документ компании

- [ ] **Журнал учёта обращений субъектов ПД**
      Уже реализован в системе (personal_data.access_log)

- [ ] **Согласие клиентов на обработку ПД**
      Форма в момент сдачи телефона. Скан хранится в системе.

- [ ] **Договор с хостингом** с указанием места обработки ПД
      Запросить у Selectel/Timeweb как оператора обработки данных

### Категория ПД и уровень защиты
Паспортные данные = специальная категория (ст. 10 152-ФЗ)
Уровень защищённости: **УЗ-3** (приказ ФСТЭК №21)
Меры: шифрование, разграничение доступа, журналирование ✓

---

## 8. Мониторинг

```bash
# Просмотр логов
docker compose logs -f backend
docker compose logs -f nginx

# Состояние сервисов
docker compose ps

# Размер БД и хранилища
docker compose exec postgres psql -U phonebase -c "\l+"
du -sh /mnt/pd_secure/
```
