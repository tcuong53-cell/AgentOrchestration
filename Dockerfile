ARG BUILD_VERSION=1.0.0

FROM node:18-alpine as builder
# Loại bỏ `ARG API_KEY_SECRET` khỏi Dockerfile. Các thông tin nhạy cảm (secrets) không nên
# được định nghĩa trực tiếp trong Dockerfile, đặc biệt là với giá trị mặc định.
# Thay vào đó, nó sẽ được truyền vào trong quá trình build bằng BuildKit's `--secret`.

WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .

# Để đảm bảo API_KEY_SECRET không bị rò rỉ vào lịch sử lớp Docker
# và không xuất hiện trong metadata của image cuối cùng:
# 1. Sử dụng `--mount=type=secret` của BuildKit để gắn secret dưới dạng một file tạm thời.
#    Điều này ngăn secret được lưu trữ trong bất kỳ lớp nào của image.
# 2. Giả định rằng lệnh `npm run build` đã được cập nhật để đọc API key từ đường dẫn file này
#    (ví dụ: thông qua một đối số như `--api-key-file`). Đây là cách an toàn nhất
#    để sử dụng secret trong quá trình build mà không làm lộ giá trị của nó trong lịch sử lệnh `RUN`.
RUN --mount=type=secret,id=api_key,target=/run/secrets/api_key_file \
    npm run build -- --version=${BUILD_VERSION} --api-key-file=/run/secrets/api_key_file

FROM nginx:alpine as final
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
# Thêm phiên bản build làm nhãn metadata (label) vào image cuối cùng.
# Điều này giúp theo dõi phiên bản một cách rõ ràng và an toàn,
# đồng thời đáp ứng yêu cầu "Runtime labels include only approved non-sensitive metadata".
LABEL org.opencontainers.image.version=${BUILD_VERSION}
CMD ["nginx", "-g", "daemon off;"]