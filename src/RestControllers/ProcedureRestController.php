<?php

/**
 * ProcedureRestController
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Matthew Vita <matthewvita48@gmail.com>
 * @author    Yash Bothra <yashrajbothra786gmail.com>
 * @copyright Copyright (c) 2018 Matthew Vita <matthewvita48@gmail.com>
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\RestControllers;

use OpenApi\Attributes as OA;
use OpenEMR\RestControllers\RestControllerHelper;
use OpenEMR\Services\ProcedureService;

class ProcedureRestController
{
    private $procedureService;

    public function __construct()
    {
        $this->procedureService = new ProcedureService();
    }

    /**
     * Fetches a single procedure resource by id.
     * @param $uuid- The procedure uuid identifier in string format.
     */
    #[OA\Get(
        path: '/api/procedure/{uuid}',
        description: 'Retrieves a procedure',
        tags: ['standard'],
        parameters: [
            new OA\Parameter(
                name: 'uuid',
                in: 'path',
                description: 'The uuid for the procedure.',
                required: true,
                schema: new OA\Schema(type: 'string')
            ),
        ],
        responses: [
            new OA\Response(response: '200', ref: '#/components/responses/standard'),
            new OA\Response(response: '400', ref: '#/components/responses/badrequest'),
            new OA\Response(response: '401', ref: '#/components/responses/unauthorized'),
        ],
        security: [['openemr_auth' => []]]
    )]
    public function getOne($uuid)
    {
        $processingResult = $this->procedureService->getOne($uuid);

        if (!$processingResult->hasErrors() && count($processingResult->getData()) == 0) {
            return RestControllerHelper::handleProcessingResult($processingResult, 404);
        }

        return RestControllerHelper::handleProcessingResult($processingResult, 200);
    }

    /**
     * Returns procedure resources which match an optional search criteria.
     */
    #[OA\Get(
        path: '/api/procedure',
        description: 'Retrieves a list of all procedures',
        tags: ['standard'],
        responses: [
            new OA\Response(response: '200', ref: '#/components/responses/standard'),
            new OA\Response(response: '400', ref: '#/components/responses/badrequest'),
            new OA\Response(response: '401', ref: '#/components/responses/unauthorized'),
        ],
        security: [['openemr_auth' => []]]
    )]
    public function getAll($search = [])
    {
        $processingResult = $this->procedureService->getAll($search);
        return RestControllerHelper::handleProcessingResult($processingResult, 200, true);
    }

    /**
     * Inserts lab result facts extracted from an uploaded document (Week 2 intake-extractor
     * worker), linking each result back to its source document via procedure_result.document_id.
     * Not a general-purpose lab-order-entry endpoint -- narrowly scoped to this one ingestion flow.
     */
    #[OA\Post(
        path: '/api/patient/{pid}/procedure_result_from_document',
        description: 'Inserts lab result facts extracted from an uploaded document, linked back to the source document.',
        tags: ['standard'],
        parameters: [
            new OA\Parameter(
                name: 'pid',
                in: 'path',
                description: 'The patient pid.',
                required: true,
                schema: new OA\Schema(type: 'integer')
            ),
        ],
        requestBody: new OA\RequestBody(
            content: new OA\JsonContent(
                required: ['document_id', 'results'],
                properties: [
                    new OA\Property(property: 'document_id', description: 'documents.id of the already-uploaded source document', type: 'integer'),
                    new OA\Property(property: 'encounter_id', description: 'Optional form_encounter.encounter', type: 'integer'),
                    new OA\Property(
                        property: 'results',
                        type: 'array',
                        items: new OA\Items(
                            type: 'object',
                            properties: [
                                new OA\Property(property: 'test_name', type: 'string'),
                                new OA\Property(property: 'value', type: 'string'),
                                new OA\Property(property: 'unit', type: 'string'),
                                new OA\Property(property: 'reference_range', type: 'string'),
                                new OA\Property(property: 'collection_date', type: 'string'),
                                new OA\Property(property: 'abnormal_flag', type: 'boolean'),
                                new OA\Property(property: 'result_code', description: 'LOINC code, if known', type: 'string'),
                            ]
                        )
                    ),
                ]
            )
        ),
        responses: [
            new OA\Response(response: '200', ref: '#/components/responses/standard'),
            new OA\Response(response: '400', ref: '#/components/responses/badrequest'),
            new OA\Response(response: '401', ref: '#/components/responses/unauthorized'),
        ],
        security: [['openemr_auth' => []]]
    )]
    public function postResultsFromDocument($pid, $data)
    {
        $documentId = (int) ($data['document_id'] ?? 0);
        $results = $data['results'] ?? [];
        $encounterId = (int) ($data['encounter_id'] ?? 0);

        if ($documentId <= 0 || empty($results) || !is_array($results)) {
            return RestControllerHelper::responseHandler(
                ['validationErrors' => ['document_id (int > 0) and a non-empty results array are required']],
                null,
                400
            );
        }

        $result = $this->procedureService->insertResultsFromDocument((int) $pid, $documentId, $results, $encounterId);
        return RestControllerHelper::responseHandler($result, null, 200);
    }
}
