# Integración con el template raíz (`aws/template.yaml`)

Tres piezas: el stack anidado, la ruta en el `DefinitionBody` y el permiso de invocación.
La Lambda también valida el grupo `operadores` (403 si no), así que la ruta es segura aunque se
cambie el autorizador.

## 1. Stack anidado

```yaml
  Analitica:
    Type: AWS::Serverless::Application
    Properties:
      Location: stacks/analitica.yaml
      Parameters:
        Stage: !Ref Stage
        ComunLayerArn: !Ref ComunLayer   # nombre lógico real de la capa en el raíz
```

## 2. Ruta en `DefinitionBody.paths` (junto a `/antenas`)

```yaml
          /analitica/{reporte}:
            get:
              summary: Reportes de analítica (solo operadores)
              security:
                - CognitoAuth: []
              parameters:
                - { name: reporte, in: path, required: true, schema: { type: string, enum: [top_zonas, top_antenas, tipos_problema, mttr, colectivos, recurrencia] } }
                - { name: dias, in: query, required: false, schema: { type: integer, minimum: 1, maximum: 90, default: 7 } }
              responses:
                '200':
                  description: OK
              x-amazon-apigateway-integration:
                type: aws_proxy
                httpMethod: POST
                uri: !Sub arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${Analitica.Outputs.ApiAnaliticaArn}/invocations
```

Se incluye `enum` solo como documentación; con `validateRequestParameters: false` (el validador
`cuerpo` actual) no se aplica en el gateway y la Lambda responde 404/400 por su cuenta.

## 3. Permiso para que API Gateway invoque la Lambda

```yaml
  PermisoApiAnalitica:
    Type: AWS::Lambda::Permission
    Properties:
      Action: lambda:InvokeFunction
      FunctionName: !GetAtt Analitica.Outputs.ApiAnaliticaArn
      Principal: apigateway.amazonaws.com
      SourceArn: !Sub arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${Api}/*/GET/analitica/*
```

## Notas

- El stack raíz debe tener el bus `telecorp-${Stage}-eventos` antes que `Analitica`
  (`DependsOn: BusEventos`, con el nombre lógico que corresponda): la regla de analítica se
  crea sobre ese bus por nombre.
- `DefinitionBody` referencia `Analitica.Outputs...`, lo que ya crea la dependencia
  Api -> Analitica; el permiso no genera ciclo porque `${Api}` solo se usa en `SourceArn`.
- Despliegue con `CAPABILITY_AUTO_EXPAND` (ya está en `samconfig.toml`). El stack anidado
  necesita `sam build` desde `aws/` para empaquetar `analitica/` y `src/api_analitica/`.
