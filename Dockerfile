# Stage 1: Build the app
FROM maven:3.9.6-eclipse-temurin-17 AS builder
WORKDIR /app
COPY pom.xml .
RUN mvn dependency:go-offline
COPY src ./src
RUN mvn clean package -DskipTests

EXPOSE 8080

# Stage 2: Run the app
FROM openjdk:17
COPY --from=builder /app/target/*.jar app.jar
COPY centralized-configuration /config
ENTRYPOINT ["java", "-jar", "app.jar"]
